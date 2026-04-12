---
name: prax-tdd-workflow
description: Prax 风格的 TDD 工作流，强调 RED → GREEN → VERIFY。
---

# Prax TDD Workflow

原则：
- 先写失败测试
- 再做最小修复
- 最后跑验证闭环

最小流程：
1. 写测试
2. 运行测试确认 RED
3. 修改实现
4. 运行测试确认 GREEN
5. 运行相关 build/typecheck/lint

不要做的事：
- 没确认 RED 就改生产代码
- 用"推测会通过"代替真实执行
