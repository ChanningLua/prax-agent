---
name: chinese-coding
description: 中文编程优化 — 注释、文档和错误信息使用中文
allowed-tools: [Read, Write, Edit, Bash]
model: glm-5
triggers: [中文, 中文注释, 中文文档, chinese, 注释, 文档, commit message, 提交信息]
tags: [chinese, localization, documentation]
priority: 8
---

# 中文编程技能

## 规则

- 所有注释和文档字符串使用中文
- 变量名和函数名使用英文（遵循 PEP 8 / camelCase），但需有中文注释说明用途
- 错误信息和日志输出使用中文
- 提交信息（commit message）使用中文
- README 和文档文件使用中文

## 代码风格

```python
# 用户认证模块
class UserAuth:
    """用户认证管理器，处理登录、注销和权限验证。"""

    def verify_token(self, token: str) -> bool:
        """验证 JWT token 是否有效。

        Args:
            token: 待验证的 JWT 字符串

        Returns:
            True 表示 token 有效，False 表示无效或已过期
        """
        ...
```

## 注意事项

- 技术术语（API、HTTP、JSON 等）保持英文
- 代码中的字符串常量如果面向用户，使用中文
- 测试用例的描述使用中文
