---
name: prax-verification-loop
description: 在提交、交付或大型改动后运行 Prax 风格的验证闭环。
---

# Prax Verification Loop

适用场景：
- 大改动完成后
- 提交前
- 修复工程脚本、门禁、集成链路后

最小验证顺序：
1. `build`
2. `typecheck`
3. `lint`
4. `test`
5. `diff review`

要求：
- 优先使用仓库现有入口
- 只报告真实执行结果
- 若某一步无法执行，明确说明原因和影响
