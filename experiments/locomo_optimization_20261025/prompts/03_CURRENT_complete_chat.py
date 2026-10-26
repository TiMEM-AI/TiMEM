async def complete_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout_sec: float = 600.0,
) -> str:
    if not api_key or not model:
        raise RuntimeError("Answer/judge model is not configured")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return str(response.json()["choices"][0]["message"]["content"]).strip()
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.NetworkError) as exc:
            last_error = exc
            if attempt == 2:
                raise
    raise last_error or RuntimeError("chat completion failed")
