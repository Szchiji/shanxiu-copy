class HetaiError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class InsufficientPointsError(HetaiError):
    def __init__(self, current: int, requested: int):
        super().__init__(f"积分不足：需要 {requested}，当前 {current}", 409)
        self.current = current
        self.requested = requested


class TenantError(HetaiError):
    def __init__(self, message: str = "用户不属于该群，无法操作积分"):
        super().__init__(message, 403)


class NotFoundError(HetaiError):
    def __init__(self, message: str = "记录不存在"):
        super().__init__(message, 404)


class ForbiddenError(HetaiError):
    def __init__(self, message: str = "没有权限"):
        super().__init__(message, 403)
