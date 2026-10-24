# Hackathon 项目

Python + Streamlit + Claude API

## 文件说明

| 文件 | 做什么 |
|---|---|
| `app.py` | 网页界面（Streamlit） |
| `ai.py` | 调用 Claude 的逻辑，`SYSTEM_PROMPT` 在这里改 |
| `test_api.py` | 检查 API key 能不能用 |
| `.env` | 放你的 API key（**不会上传到 GitHub**，每个人自己建） |

## 第一次设置（每个人在自己电脑做一次）

```powershell
git clone https://github.com/Jerry-0821/hackathon-2026.git
cd hackathon-2026
powershell -ExecutionPolicy Bypass -File setup.ps1
```

`setup.ps1` 会自动建虚拟环境、装依赖、建 `.env`。然后打开 `.env`，填入 API key。

> 如果 `Activate.ps1` 报错 "running scripts is disabled"，先运行：
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## 每次开工

```powershell
.venv\Scripts\Activate.ps1
python test_api.py      # 检查 key（可选）
streamlit run app.py    # 打开网页
```

改完代码保存，网页右上角点 **Rerun** 就能看到效果。

## 两个人合作

- 开工前先 `git pull`，改完 `git add .` → `git commit -m "说明"` → `git push`
- 尽量分开改不同文件：一个人管 `app.py`（界面），一个人管 `ai.py`（AI 逻辑）
- API key 用私信发，**不要写进代码**

## 换模型

`ai.py` 里的 `MODEL`：

- `claude-opus-5-5`：效果最好（默认）
- `claude-sonnet-5-5`：价格约一半
- `claude-haiku-5-5`：最便宜、最快
