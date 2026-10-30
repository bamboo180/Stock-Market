股票訊號看板（台股 + 美股）
================================

【啟動】
Windows：雙擊「啟動.bat」，瀏覽器會自動打開 http://localhost:8501
  ※ 更新版本時：先關掉舊的黑色視窗，再把新檔案覆蓋到同一個資料夾，雙擊「啟動.bat」
    （新版的啟動.bat 也會自動關掉還在跑的舊版）。左側欄最下面會顯示版本號，可確認是不是新版
其他系統或手動：
  pip install -r requirements.txt
  streamlit run app.py

【使用】
0. 開啟後是「📈 市場總覽」：
   上方「市場指數」：預設加權、櫃買、S&P 500、那斯達克、道瓊、費城半導體，附近一個月走勢
     展開「調整指數」可新增或移除；內建日經、恆生、VIX、美債殖利率、匯率、黃金、比特幣等，
     清單外可直接輸入 Yahoo Finance 代號（例如 ^FTSE）按 Enter
     每張指數卡下方「K 線與分析 ›」可進入該指數的 K 線、評分與新聞
   下方「我的最愛」：現價、漲跌、明日／10 日上漲機率、停損警示一次看
   台股漲停時現價紅底白字、跌停綠底白字（盤後整理的排行表也一樣）
   點表格任一列左邊的方框 → 開啟該股分析
   「管理清單」可以新增或移除
★ 任何頁面左上角「🏠 回首頁」可回到市場總覽
0-1. 「📰 盤後整理」：
   台股：加權／櫃買、成交金額、漲跌家數、三大法人與融資、類股漲跌、主要類股指數、
         漲幅／跌幅／成交金額前 10、我的最愛今日表現（收盤後約 14:30 起有當天資料）
   美股：四大指數、VIX、10 年期殖利率、美元指數、11 大類股、大型權值股、漲跌排行、
         台積電 ADR 溢價、我的最愛今日表現
   排行表點任一列可直接開啟該股分析；設定 ANTHROPIC_API_KEY 後可請 Claude 寫盤後評論
1. 左側「🔍 搜尋股票」：打幾個字就會跳出符合的選項，點選即可
   可用代號、中文名稱或產業搜尋，例如：2330、台積、半導體、高股息、輝達、蘋果、MSFT
   清單外的美股：直接打代號（例如 PLTR）或英文公司名，按 Enter
2. 或點左側「我的最愛」按鈕；個股頁右上角「☆ 加入最愛」可收藏
3. 個股頁上方：「下一個交易日」與「未來 10 個交易日」的上漲／下跌機率
   展開「📊 機率表」可看每個因素（技術、籌碼、營收、新聞、隔夜美股）各加減幾個百分點
   台股收盤後會依前一晚那斯達克、費半（美股未收盤時用那斯達克期貨）推估明天開盤漲跌
   分頁：K 線與預估／籌碼‧量能／回測驗證／說明
4. K 線操作：拖曳平移、滾輪（手機雙指）縮放、上方按鈕切換 1月/3月/6月/1年/全部，
   游標移到 K 棒上方顯示開高低收與均線；雙擊右側價格軸復原
6. 「💼 我的持倉分析」：輸入買進價（可選填股數、買進日、可承受虧損%），按「儲存並分析」
   會算出損益（含手續費與稅）、建議停損、風險報酬比，並依趨勢給出續抱／減碼／出場建議
   買進價會存起來，K 線上會畫出紫色「我的成本」線，最愛總覽也會顯示成本與持倉損益
   有持倉時，畫面上的停損價都會改用你持倉的停損
5. 「🏢 公司資訊」分頁（在 K 線與預估、籌碼／量能旁邊）：維基百科簡介、董事長／總經理、成立與上市日期、資本額、市值、
   本益比／殖利率／股價淨值比、毛利率／營益率／淨利率、每季 EPS 長條圖、近期股利
   （美股：產業、員工數、市值、本益比、52 週區間、Yahoo 英文簡介）
   右側「📰 重點消息」：近 7 天新聞，台股另含官方重大訊息；「只看重點」只留財報、營收、
   法說、股利、評等、籌碼、營運、風險相關的標題

【檔案】
app.py        介面
engine.py     抓資料、評分、模型、回測、預估扇形
kchart.py     互動 K 線圖（TradingView Lightweight Charts）
news.py       新聞抓取與重點標記
live.py       即時報價與交易時段
store.py      我的最愛與持倉儲存
symbols.py    股票清單與關鍵字搜尋
position.py   持倉損益與停損計算
indices.py    市場指數
recap.py      台股／美股盤後整理
prob.py       上漲／下跌機率與機率表、明日開盤預估
company.py    公司基本資料與簡介
data/         內建台股清單（FinMind 抓不到時使用）、美股常見代號中英文對照
requirements.txt  需要的套件

【資料來源與限制】
台股：FinMind 免費 API（日K、三大法人、融資融券；大盤法人與融資合計）
      證交所每日收盤行情（盤後整理用）、櫃買中心開放資料（日期相符時併入排行）
      不填 token 每小時次數有限；到 finmindtrade.com 免費註冊可提高額度
美股：Yahoo Finance（yfinance）。美股沒有每日法人資料，改用 OBV、RSI、相對大盤
即時報價：台股用證交所基本市況報導站（約 5 秒延遲），美股用 Yahoo（可能延遲至 15 分鐘）

【速度】
進入個股頁時，K 線、新聞、營收、公司資料、機率會同時抓取（不再一個等一個）；
開啟首頁後會在背景預先載入最愛股票、盤後整理與隔夜美股，之後點進去幾乎立即顯示。
第一次開啟或按「清除快取」後需要幾秒抓資料，之後 15～30 分鐘內都是直接顯示。

【自動更新】
開著頁面時，盤中（台股 9:00-13:30、美股 21:30-04:00 台灣時間，冬令時間晚 1 小時）
報價每 60 秒自動更新，跌破停損參考會顯示 ⚠。
歷史資料與評分每 30 分鐘重新抓取，收盤資料公布後自動換成新的一天。
評分只用收盤資料計算，盤中不會跳動（跟回測條件一致）；K 線上的空心 K 棒是今天盤中暫定值。
要強制更新請按「清除快取並重新抓取」

本工具為個人研究用技術原型，不構成投資建議。

【token 設定】
.streamlit/secrets.toml 已寫好你的 FinMind token，程式會直接讀取（畫面上不再顯示輸入框）。
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

【停損怎麼算】
虧損中：取「成本 ×（1 − 可承受虧損%）」與「近 20 日最低價 − 0.5×ATR」中較高的一個
獲利中：移動停損 =「買進後最高收盤 − 2.5×ATR」；獲利超過 2×ATR 後至少拉到損益兩平
ATR 是近 14 日平均真實波幅，代表這檔股票正常一天會晃多少。買進日要填，才能算買進後最高價。
若設定了 ANTHROPIC_API_KEY，持倉區會多一個「讓 Claude 分析這筆交易」按鈕（依用量計費）。
