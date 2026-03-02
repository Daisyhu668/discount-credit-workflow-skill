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

## 3. 推送到 GitHub

如果你已在本机配置好 SSH 或 PAT：

```bash
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

## 4. OpenCloud 安装方式

1. 在 OpenCloud 挂载 GitHub 仓库。
2. 将技能路径指向：
   - `/workspace/.../discount-credit-workflow`
3. 执行健康检查命令：

```bash
python scripts/run_discount_pipeline.py assets/input_sample_minimal.json --yes --output 输出结果
```

## 5. 版本建议

- Git Tag：`v1.0.0`, `v1.1.0`（避免直接追 main）
- OpenCloud 固定安装到 tag 版本，减少流程漂移
