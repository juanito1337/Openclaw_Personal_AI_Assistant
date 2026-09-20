from __future__ import annotations

from dataclasses import dataclass

from .run_contract import result_from_exit_code


@dataclass(frozen=True, slots=True)
class JobRunOutcome:
    result: str
    business_status: str
    consecutive_failures: int
    batch_resume: bool
    resume_parent_run_id: str
    last_success: bool


def resolve_job_run(
    code: int,
    *,
    stopped: bool,
    lease_lost: bool,
    previous_failures: int,
    claim_run_id: str = "",
    batch_resume_exit_code: int = 75,
) -> JobRunOutcome:
    """Resolve one worker result without hiding stop or lease-loss failures."""

    interrupted = stopped or lease_lost
    batch_resume = code == batch_resume_exit_code and not interrupted
    result = "completed" if batch_resume else result_from_exit_code(code, interrupted=interrupted)
    if interrupted:
        business_status = "interrupted"
        consecutive_failures = previous_failures + 1
    elif code == 0 or batch_resume:
        business_status = "healthy"
        consecutive_failures = 0
    elif code == 1:
        business_status = "degraded"
        consecutive_failures = previous_failures + 1
    else:
        business_status = "failed"
        consecutive_failures = previous_failures + 1
    return JobRunOutcome(
        result=result,
        business_status=business_status,
        consecutive_failures=consecutive_failures,
        batch_resume=batch_resume,
        resume_parent_run_id=claim_run_id if batch_resume else "",
        last_success=code == 0 and not interrupted,
    )
