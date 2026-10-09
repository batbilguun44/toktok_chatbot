"""Tracing / evaluation hooks (Langfuse when configured, no-op otherwise).

Spans are parented explicitly instead of through "current span" context
because a turn is processed inside a streaming async generator.
"""

import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Observation(Protocol):
    def child(self, name: str, as_type: str = "span", **kw: Any) -> "Observation": ...
    def update(self, **kw: Any) -> None: ...
    def end(self) -> None: ...


class Tracer(Protocol):
    def start_turn(self, *, turn_id: str, session_id: str, user_ref: str, persona: str, input: Any) -> Observation: ...
    def score_feedback(self, *, turn_id: str, rating: int, comment: str | None) -> None: ...
    def score_judgement(self, *, turn_id: str, grounded: str, score: int) -> None: ...
    def flush(self) -> None: ...


class _NoopObservation:
    def child(self, name: str, as_type: str = "span", **kw: Any) -> "_NoopObservation":
        return self

    def update(self, **kw: Any) -> None:
        pass

    def end(self) -> None:
        pass


class NoopTracer:
    def start_turn(self, **kw: Any) -> Observation:
        return _NoopObservation()

    def score_feedback(self, **kw: Any) -> None:
        pass

    def score_judgement(self, **kw: Any) -> None:
        pass

    def flush(self) -> None:
        pass


class _LangfuseObservation:
    def __init__(self, obs: Any):
        self._obs = obs

    def child(self, name: str, as_type: str = "span", **kw: Any) -> "_LangfuseObservation":
        try:
            return _LangfuseObservation(self._obs.start_observation(name=name, as_type=as_type, **kw))
        except Exception as e:  # tracing must never break chat
            log.debug("langfuse child failed: %s", e)
            return _LangfuseObservation(None)

    def update(self, **kw: Any) -> None:
        if self._obs is not None:
            try:
                self._obs.update(**kw)
            except Exception as e:
                log.debug("langfuse update failed: %s", e)

    def end(self) -> None:
        if self._obs is not None:
            try:
                self._obs.end()
            except Exception as e:
                log.debug("langfuse end failed: %s", e)


class LangfuseTracer:
    def __init__(self, public_key: str, secret_key: str, base_url: str | None, environment: str):
        from langfuse import Langfuse

        self.lf = Langfuse(public_key=public_key, secret_key=secret_key, base_url=base_url, environment=environment)

    def start_turn(self, *, turn_id: str, session_id: str, user_ref: str, persona: str, input: Any) -> Observation:
        from langfuse import propagate_attributes

        try:
            trace_id = self.lf.create_trace_id(seed=turn_id)
            with propagate_attributes(user_id=user_ref, session_id=session_id, tags=[persona], trace_name="chat_turn"):
                root = self.lf.start_observation(
                    trace_context={"trace_id": trace_id}, name="chat_turn", as_type="agent",
                    input=input, metadata={"persona": persona, "turn_id": turn_id},
                )
            return _LangfuseObservation(root)
        except Exception as e:
            log.warning("langfuse start_turn failed: %s", e)
            return _NoopObservation()

    def score_feedback(self, *, turn_id: str, rating: int, comment: str | None) -> None:
        try:
            self.lf.create_score(
                name="user_feedback", value=float(rating), data_type="NUMERIC",
                trace_id=self.lf.create_trace_id(seed=turn_id), comment=comment,
            )
        except Exception as e:
            log.warning("langfuse score failed: %s", e)

    def score_judgement(self, *, turn_id: str, grounded: str, score: int) -> None:
        try:
            trace_id = self.lf.create_trace_id(seed=turn_id)
            self.lf.create_score(name="judge_grounded", value=grounded, data_type="CATEGORICAL", trace_id=trace_id)
            self.lf.create_score(name="judge_score", value=float(score), data_type="NUMERIC", trace_id=trace_id)
        except Exception as e:
            log.warning("langfuse judge score failed: %s", e)

    def flush(self) -> None:
        self.lf.flush()
