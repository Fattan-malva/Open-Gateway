"""Error berbentuk OpenAI agar klien seperti OpenCode bisa menampilkannya."""
from typing import Optional


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        error_type: str = "invalid_request_error",
        code: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type
        self.code = code

    def to_dict(self) -> dict:
        error = {"message": self.message, "type": self.error_type}
        if self.code:
            error["code"] = self.code
        return {"error": error}
