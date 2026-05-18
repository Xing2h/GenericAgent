# GitHub Contribution SOP
**触发**：需要给开源项目提 PR。**禁用**：仅读代码或不提交变更。

## 只保留硬规则
1. 先读项目规范：`CONTRIBUTING.md` / PR 模板 / README Contributing；
2. 一个 PR 只做一件事；改动最小化，跟随项目风格。
3. 先确认测试命令并实际运行；测试不过不推代码。PR 正文必须写 What / Why / Testing。
4. Review 后按要求补测试并追加 commit；除非 maintainer 明确要求，否则不要 force push 覆盖 review 历史。

## 已验证 Git/PowerShell 坑
- PowerShell 中 `stash@{0}` 要加引号：`git stash apply 'stash@{0}'`。
- 冲突标记删完但 `git status` 仍 `UU`：还需 `git add 冲突文件` 才算 resolved。
- 冲突已解决且已 `git add`，但 `git merge --continue --no-edit` 不通过：可 `git commit --no-edit` 完成 merge。

