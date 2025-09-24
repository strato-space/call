# proxy_env_test.py
import httpx

def test_httpx_env():
    print("[info] httpx will honor HTTP(S)_PROXY/ALL_PROXY from environment")
    with httpx.Client(timeout=15.0) as client:
        # Проверка публичного IP
        ip = client.get("https://api.ipify.org?format=json").text
        print("ipify:", ip)

        # Проверка OpenAI /v1/models
        r = client.get("https://api.openai.com/v1/models")
        print("status:", r.status_code)
        print("headers:", dict(r.headers))
        print("body:", r.text[:400])

if __name__ == "__main__":
    test_httpx_env()
