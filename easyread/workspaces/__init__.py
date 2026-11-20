"""Independent, persistent research projects (not the paper reading workspace)."""


class WorkspaceError(ValueError):
    def __init__(self, message, status=400, code="invalid_request"):
        super().__init__(message)
        self.status = status
        self.code = code
