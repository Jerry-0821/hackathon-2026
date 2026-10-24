"""运行 python test_api.py 检查 API key 能不能用。"""
import ai

if not ai.has_api_key():
    print("❌ 找不到 ANTHROPIC_API_KEY，先建 .env 文件（参考 .env.example）")
else:
    print(ai.ask("用一句话介绍你自己"))
    print("\n✅ API 能用了！")
