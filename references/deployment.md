# 发布部署（GitHub + OpenCloud）

## 1. 本地打包

```bash
python /Users/daisy/.codex/skills/skill-creator/scripts/package_skill.py \
  /Users/daisy/gemini\ demo/工作/工具/贴现/discount-credit-workflow \
  /Users/daisy/gemini\ demo/skill_dist
```

## 2. Git 仓库初始化

```bash
cd /Users/daisy/gemini\ demo/工作/工具/贴现/discount-credit-workflow
git init
git add .
git commit -m "feat: package discount credit workflow skill"
```

## 3. 推送到 GitHub（建议公共仓库）

如果你已在本机配置好 SSH 或 PAT：

```bash
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

## 4. OpenClaw / OpenCloud 一键安装

方式 A（推荐）：GitHub 公共仓库

1. 打开 OpenClaw / OpenCloud 的 Skills 安装页面。
2. 选择 “从 GitHub 安装”。
3. 仓库地址填写：
   - `https://github.com/Daisyhu668/discount-credit-workflow-skill`
4. 技能路径填写：
   - `discount-credit-workflow`
5. 版本选择：
   - 优先选择 tag，例如 `v1.1.1`（避免直接追 main）
6. 点击安装。

方式 B：`.skill` 包导入

1. 打开 Skills 安装页面。
2. 选择 “上传 .skill 文件”。
3. 选择本地 `skill_dist/discount-credit-workflow.skill` 导入。

## 5. 安装后验证

1. 在 OpenCloud 挂载 GitHub 仓库。
2. 将技能路径指向：
   - `/workspace/.../discount-credit-workflow`
3. 执行健康检查命令：

```bash
python scripts/run_discount_pipeline.py assets/input_sample_minimal.json --yes --output 输出结果
```

## 6. 版本建议

- Git Tag：`v1.0.0`, `v1.1.0`（避免直接追 main）
- OpenCloud 固定安装到 tag 版本，减少流程漂移
