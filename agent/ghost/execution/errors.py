from uuid import uuid4


class GhostError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status

    def wire(self):
        return {"error": {"code": self.code, "message": self.message,
                          "retryable": False, "trace_id": str(uuid4())}}
