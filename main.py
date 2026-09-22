import requests

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma4:26b"


def ask_ollama(question: str) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": question}],
            "stream": False,
        },
    )
    response.raise_for_status()
    print(response.json())
    return response.json()["message"]["content"]


if __name__ == "__main__":
    answer = ask_ollama("Qui est le president des USA ?")
    print(answer)
