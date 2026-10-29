股票訊號看板（台股 + 美股）
================================

【啟動】
Windows：雙擊「啟動.bat」，瀏覽器會自動打開 http://localhost:8501
其他系統或手動：
  pip install -r requirements.txt
  streamlit run app.py

【使用】
0. 開啟後先看到「⭐ 我的最愛」總覽：現價、漲跌、評分、停損警示一次看
   點表格任一列左邊的方框 → 開啟該股分析
   「管理清單」可以新增或移除
1. 左側輸入股票代號，按「分析」
   台股：2330、2317、0050（數字代號，上市上櫃都可）
   美股：AAPL、NVDA、TSLA、QQQ、BRK-B（英文代號）
2. 或點左側「我的最愛」按鈕；個股頁右上角「☆ 加入最愛」可收藏
3. 分頁：K 線與預估／籌碼‧量能／回測驗證／說明
4. K 線操作：拖曳平移、滾輪（手機雙指）縮放、上方按鈕切換 1月/3月/6月/1年/全部，
   游標移到 K 棒上方顯示開高低收與均線；雙擊右側價格軸復原
5. 右側「重點消息」：近 7 天新聞，台股另含官方重大訊息；「只看重點」只留財報、營收、
   法說、股利、評等、籌碼、營運、風險相關的標題

【檔案】
app.py        介面
engine.py     抓資料、評分、模型、回測、預估扇形
kchart.py     互動 K 線圖（TradingView Lightweight Charts）
news.py       新聞抓取與重點標記
live.py       即時報價與交易時段
store.py      我的最愛儲存
requirements.txt  需要的套件

【資料來源與限制】
台股：FinMind 免費 API（日K、三大法人、融資融券）
      不填 token 每小時次數有限；到 finmindtrade.com 免費註冊可提高額度
美股：Yahoo Finance（yfinance）。美股沒有每日法人資料，改用 OBV、RSI、相對大盤
即時報價：台股用證交所基本市況報導站（約 5 秒延遲），美股用 Yahoo（可能延遲至 15 分鐘）

【自動更新】
開著頁面時，盤中（台股 9:00-13:30、美股 21:30-04:00 台灣時間，冬令時間晚 1 小時）
報價每 60 秒自動更新，跌破停損參考會顯示 ⚠。
歷史資料與評分每 30 分鐘重新抓取，收盤資料公布後自動換成新的一天。
評分只用收盤資料計算，盤中不會跳動（跟回測條件一致）；K 線上的空心 K 棒是今天盤中暫定值。
要強制更新請按「清除快取並重新抓取」

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

【我的最愛同步（雲端版建議設定）】
在自己電腦跑：最愛存在 favorites.json，不用設定。
在 Streamlit Cloud：雲端主機重開時檔案會不見，建議改存到 GitHub，手機電腦也能共用同一份：
1. GitHub 右上頭像 → Settings → Developer settings → Personal access tokens
   → Fine-grained tokens → Generate new token
2. Repository access 選 Only select repositories → 選 stock-signal-app
3. Permissions → Repository permissions → Contents 設為 Read and write
4. 產生後複製 token，在 Streamlit 的 App settings → Secrets 加上：
     GITHUB_TOKEN = "github_pat_開頭的那串"
     GITHUB_REPO = "你的GitHub帳號/stock-signal-app"
5. 側邊欄底部會顯示「最愛儲存於：GitHub」
最愛會存在 repo 的 favorites-data 分支，不會影響程式碼，也不會觸發重新部署。

【AI 新聞摘要（選用，需付費）】
在 secrets.toml（雲端則是 Secrets）加上 Anthropic API key：
  ANTHROPIC_API_KEY = "sk-ant-..."
新聞欄就會出現「✨ AI 整理重點」按鈕，由 Claude 把近一週標題整理成 3～5 點利多／利空重點。
只有按按鈕才會呼叫，結果快取 1 小時。API 依用量計費，到 console.anthropic.com 申請。
沒設定也沒關係，新聞列表和重點標記照常可用。
