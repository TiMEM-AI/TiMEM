# TiMem 页面

聊天页只调用 `server` 的 HTTP 接口。

```bash
cd frontend
npm install
npm run dev
```

开发地址是 http://127.0.0.1:5173 ，`/v1` 会代理到 http://127.0.0.1:8000 。请先在仓库根目录运行 `python -m server`。

构建产物在 `frontend/dist`。`server` 发现这个目录后，会在 `/` 提供页面。
