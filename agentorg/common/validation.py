"""Task input validation shared by agents (mirrors astrolabe)."""

MAX_TASK_LEN = 4000


def validate_task(task: str) -> str:
    task = (task or "").strip()
    if not task:
        raise ValueError("task must not be empty")
    if len(task) > MAX_TASK_LEN:
        raise ValueError(f"task too long ({len(task)} > {MAX_TASK_LEN})")
    return task
