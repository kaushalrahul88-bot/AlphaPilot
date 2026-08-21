import gc

from .groww_dynamic import DynamicGrowwProvider


class MemorySafeGrowwProvider(DynamicGrowwProvider):
    """Groww provider tuned for Render's 512 MB free instance.

    Keeps the existing trading logic and shared auth cache, but limits retained
    indicator payloads and option snapshot state between scanner requests.
    """

    MAX_CANDLES_FOR_ANALYSIS = 260
    MAX_OPTION_SNAPSHOTS = 12

    async def _get_access_token(self):
        """Use an explicitly configured daily access token before dynamic auth.

        When GROWW_ACCESS_TOKEN is present, it is already a valid bearer token
        for the current Groww session. Avoid calling the token-generation
        endpoint first because repeated auth attempts can trigger HTTP 429 even
        though market-data requests themselves are succeeding with the configured
        token.
        """
        if self.access_token:
            return self.access_token
        return await super()._get_access_token()

    async def candles(self, symbol, timeframe="15m"):
        candles = await super().candles(symbol, timeframe)
        if isinstance(candles, list) and len(candles) > self.MAX_CANDLES_FOR_ANALYSIS:
            return candles[-self.MAX_CANDLES_FOR_ANALYSIS:]
        return candles

    def _snapshot_payload(self, symbol, expiry, raw):
        # Base implementation includes every normalized option strike in `rows`.
        # The delta calculation only needs aggregate fields, so do not retain the
        # full chain in process memory between scans.
        payload = super()._snapshot_payload(symbol, expiry, raw)
        payload.pop("rows", None)
        return payload

    async def take_option_snapshot(self, symbol, expiry=None):
        result = await super().take_option_snapshot(symbol, expiry)

        # Bound process-memory persistence even if many symbols/expiries are
        # confirmed during the day. Dict insertion order is preserved in Python.
        while len(self._option_snapshots) > self.MAX_OPTION_SNAPSHOTS:
            oldest = next(iter(self._option_snapshots))
            self._option_snapshots.pop(oldest, None)

        gc.collect()
        return result

    @staticmethod
    def _compact_tf(result):
        """Keep only fields required by MTF scoring and frontend display."""
        if not isinstance(result, dict):
            return result
        if result.get("status") == "ERROR":
            return {
                "symbol": result.get("symbol"),
                "status": "ERROR",
                "error": result.get("error"),
            }

        keys = (
            "symbol", "status", "signal", "direction", "alpha_score",
            "price", "rsi14", "market_structure", "entry", "stop_loss",
            "target1", "target2", "risk_reward", "reason",
        )
        return {k: result.get(k) for k in keys if k in result}

    async def multi_timeframe_scan(self, symbols, timeframes, min_rr):
        from app.engine import analyze_candles

        results = []
        weights = {"5m": 0.20, "15m": 0.35, "1h": 0.30, "1d": 0.15}

        for symbol in symbols:
            symbol = symbol.upper()
            tf = {}

            for timeframe in timeframes:
                candles = None
                analyzed = None
                try:
                    candles = await self.candles(symbol, timeframe)
                    analyzed = analyze_candles(symbol, candles, min_rr)
                    tf[timeframe] = self._compact_tf(analyzed)
                except Exception as exc:
                    tf[timeframe] = {
                        "symbol": symbol,
                        "status": "ERROR",
                        "error": str(exc),
                    }
                finally:
                    candles = None
                    analyzed = None

            valid = [v for v in tf.values() if v.get("status") != "ERROR"]
            if not valid:
                results.append({
                    "symbol": symbol,
                    "status": "ERROR",
                    "signal": "ERROR",
                    "timeframes": tf,
                    "error": "No usable timeframe data",
                })
                gc.collect()
                continue

            weighted_sum = sum(
                tf[t].get("alpha_score", 50) * weights.get(t, 0.25)
                for t in tf if tf[t].get("status") != "ERROR"
            )
            weight_used = sum(
                weights.get(t, 0.25)
                for t in tf if tf[t].get("status") != "ERROR"
            )
            score = weighted_sum / weight_used if weight_used else 50

            long_votes = sum(
                1 for v in valid
                if v.get("signal") in ("LONG", "STRONG_LONG", "WATCH_LONG")
                and v.get("alpha_score", 0) >= 58
            )
            short_votes = sum(
                1 for v in valid
                if v.get("signal") in ("SHORT", "STRONG_SHORT", "WATCH_SHORT")
                and v.get("alpha_score", 100) <= 42
            )
            setup_long = sum(
                1 for v in valid
                if v.get("status") == "SETUP" and v.get("direction") == "LONG"
            )
            setup_short = sum(
                1 for v in valid
                if v.get("status") == "SETUP" and v.get("direction") == "SHORT"
            )

            higher_bull = any(
                tf.get(t, {}).get("alpha_score", 50) >= 55
                for t in ("1h", "1d") if t in tf
            )
            higher_bear = any(
                tf.get(t, {}).get("alpha_score", 50) <= 45
                for t in ("1h", "1d") if t in tf
            )
            n = len(valid)

            if score >= 68 and long_votes >= max(2, n - 1) and setup_long >= 1 and not higher_bear:
                status, direction = "SETUP", "LONG"
            elif score <= 32 and short_votes >= max(2, n - 1) and setup_short >= 1 and not higher_bull:
                status, direction = "SETUP", "SHORT"
            else:
                status, direction = "NO_TRADE", None

            if status == "SETUP" and direction == "LONG":
                signal = "STRONG_LONG" if score >= 80 and long_votes == n else "LONG"
            elif status == "SETUP" and direction == "SHORT":
                signal = "STRONG_SHORT" if score <= 20 and short_votes == n else "SHORT"
            elif score >= 58:
                signal = "WATCH_LONG"
            elif score <= 42:
                signal = "WATCH_SHORT"
            else:
                signal = "NO_TRADE"

            item = {
                "symbol": symbol,
                "status": status,
                "signal": signal,
                "multi_timeframe_score": round(score, 1),
                "timeframe_votes": {"long": long_votes, "short": short_votes, "valid": n},
                "higher_timeframe": {"bullish": higher_bull, "bearish": higher_bear},
                "timeframes": tf,
            }

            if status == "SETUP":
                execution = tf.get("15m", valid[0])
                item.update({
                    "direction": direction,
                    "execution_timeframe": "15m" if "15m" in tf else timeframes[0],
                    "entry": execution.get("entry"),
                    "stop_loss": execution.get("stop_loss"),
                    "target1": execution.get("target1"),
                    "target2": execution.get("target2"),
                    "risk_reward": execution.get("risk_reward"),
                })
            else:
                item["reason"] = "Multi-timeframe alignment threshold not met"

            results.append(item)
            valid = None
            tf = None
            gc.collect()

        response = {
            "provider": "GROWW",
            "mode": "MULTI_TIMEFRAME",
            "timeframes": timeframes,
            "min_risk_reward": min_rr,
            "setups": [x for x in results if x.get("status") == "SETUP"],
            "others": [x for x in results if x.get("status") != "SETUP"],
        }
        gc.collect()
        return response
