股票訊號看板（台股 + 美股）
================================

【啟動】
Windows：雙擊「啟動.bat」，瀏覽器會自動打開 http://localhost:8501
其他系統或手動：
  pip install -r requirements.txt
  streamlit run app.py

【使用】
1. 左側輸入股票代號，按「分析」
   台股：2330、2317、0050（數字代號，上市上櫃都可）
   美股：AAPL、NVDA、TSLA、QQQ、BRK-B（英文代號）
2. 或直接點快速選擇按鈕
3. 分頁：K 線與預估／籌碼‧量能／回測驗證／說明

【檔案】
app.py        介面
engine.py     抓資料、評分、模型、回測、預估扇形
requirements.txt  需要的套件

【資料來源與限制】
台股：FinMind 免費 API（日K、三大法人、融資融券）
      不填 token 每小時次數有限；到 finmindtrade.com 免費註冊可提高額度
美股：Yahoo Finance（yfinance）。美股沒有每日法人資料，改用 OBV、RSI、相對大盤
資料抓過會快取 1 小時，要強制更新請按「清除快取並重新抓取」

本工具為個人研究用技術原型，不構成投資建議。

【token 設定】
.streamlit/secrets.toml 已寫好你的 FinMind token，啟動後側邊欄會顯示「已從設定檔載入」。
這個檔案不要上傳到 GitHub 或分享給別人（.gitignore 已排除它）。

【密碼保護（選用）】
編輯 .streamlit/secrets.toml，把 APP_PASSWORD 那行的 # 拿掉並改成你的密碼，
之後開網頁要先輸入密碼才能使用。

【部署到網路：Streamlit Community Cloud（免費）】
1. 把整個資料夾上傳到 GitHub（建議 private repository）
   secrets.toml 不會被上傳，這是正確的
2. 到 share.streamlit.io 用 GitHub 登入 → Create app → 選這個 repo，主檔案填 app.py
3. Advanced settings → Secrets，貼上：
     FINMIND_TOKEN = "你的token"
4. Deploy，完成後會得到 https://xxxx.streamlit.app 網址，手機也能開
5. App settings → Sharing → 選「Only specific people can view this app」，
   再輸入要開放的 email（免費方案只能有 1 個非公開 App）
