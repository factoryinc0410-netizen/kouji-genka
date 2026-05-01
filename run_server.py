"""
Web サーバー起動スクリプト
使い方: python run_server.py
"""
import uvicorn
from web_app.core.config import HOST, PORT

if __name__ == "__main__":
    uvicorn.run(
        "web_app.main:app",
        host=HOST,
        port=PORT,
        workers=1,       # COM 排他制御のため必ず 1 に固定
        reload=False,     # 本番環境では False（開発時のみ True に変更）
        log_level="info",
    )
