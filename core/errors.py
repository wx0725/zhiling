class ModuleError(Exception):
    """模块按约定报告的错误。code 应列在 module.json 的 errors 中。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
