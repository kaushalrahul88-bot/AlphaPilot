from __future__ import annotations

PROCESS_FIELDS=("point_in_time_clean","thesis_complete","trigger_defined","invalidation_defined",
                "exit_logic_defined","risk_reward_justified","contradictions_recorded","missing_context_explicit")

def score_decision_process(decision:dict)->dict:
    checks={
      "point_in_time_clean":decision.get("lookahead_violation") is not True,
      "thesis_complete":bool(decision.get("thesis")),
      "trigger_defined":bool(decision.get("entry_trigger")) if decision.get("action") in {"BUY_CE","BUY_PE"} else True,
      "invalidation_defined":bool(decision.get("invalidation")) if decision.get("action") in {"BUY_CE","BUY_PE"} else True,
      "exit_logic_defined":bool(decision.get("target_or_exit_logic")) if decision.get("action") in {"BUY_CE","BUY_PE"} else True,
      "risk_reward_justified":bool(decision.get("risk_reward_basis")) if decision.get("action") in {"BUY_CE","BUY_PE"} else True,
      "contradictions_recorded":"contradictions" in decision,
      "missing_context_explicit":"missing_context" in decision,
    }
    return {"checks":checks,"passed":sum(checks.values()),"total":len(checks),
            "process_pct":round(sum(checks.values())/len(checks)*100,2)}

def replay_scorecard(decisions:list[dict])->dict:
    process=[score_decision_process(x) for x in decisions]
    actions={a:sum(x.get("action")==a for x in decisions) for a in ("BUY_CE","BUY_PE","WAIT","NO_TRADE")}
    trades=[x for x in decisions if x.get("action") in {"BUY_CE","BUY_PE"}]
    def outcome_label(row):
        outcome=row.get("outcome")
        if isinstance(outcome,dict):
            return outcome.get("result") or outcome.get("outcome")
        return outcome
    resolved=[x for x in trades if outcome_label(x) in {"TARGET","STOP"}]
    wins=sum(outcome_label(x)=="TARGET" for x in resolved)
    return {"mode":"CURRENT_MIND_REPLAY_SCORECARD_V1",
      "primary_objective":"DECISION_PROCESS_AND_TRADE_EXPECTANCY_NOT_EXACT_PREDICTION",
      "decisions":len(decisions),"actions":actions,
      "mean_process_pct":round(sum(x["process_pct"] for x in process)/len(process),2) if process else None,
      "resolved_trades":len(resolved),"target_rate_resolved":round(wins/len(resolved)*100,2) if resolved else None,
      "required_future_metrics":["expectancy_R","realized_R","MFE_R","MAE_R","time_to_entry","time_to_exit",
        "confidence_calibration","regime_performance","no_trade_opportunity_cost","wait_conversion_quality"],
      "warning":"Win rate/directional accuracy alone must not be used to declare Trader Mind successful."}
