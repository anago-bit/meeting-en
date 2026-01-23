import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google import genai

# --- 環境変数から取得 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DOC_ID = os.environ.get("DOC_ID")
# GitHub Secretsに貼り付けたJSONの中身を受け取る
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON")

SCOPES = ['https://www.googleapis.com/auth/documents.readonly']

def get_credentials():
    if not SERVICE_ACCOUNT_JSON:
        raise ValueError("環境変数 SERVICE_ACCOUNT_JSON が設定されていません。")
    
    # 文字列として渡されたJSONを辞書形式に変換して認証
    info = json.loads(SERVICE_ACCOUNT_JSON)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

def read_doc(doc_id):
    creds = get_credentials()
    service = build('docs', 'v1', credentials=creds)
    document = service.documents().get(documentId=doc_id).execute()
    text = ""
    for content in document.get('body').get('content'):
        if 'paragraph' in content:
            for element in content.get('paragraph').get('elements'):
                text += element.get('textRun', {}).get('content', '')
    return text

def translate_full_text(text):
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
    以下の議事録を、内容を省略したり要約したりせずに、原文のすべての発言を網羅して翻訳してください。
    出力は「英語」と「ネパール語」の両方で行ってください。

    【ルール】
    - 要約は一切禁止です。
    - 全ての発言を一字一句漏らさず翻訳してください。
    - 形式は、日本語原文に対応するように記述してください。

    議事録内容:
    {text}
    """
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    return response.text

if __name__ == "__main__":
    try:
        print("1. ドキュメントを読み込んでいます...")
        content = read_doc(DOC_ID)
        print("2. Geminiで翻訳中（英語・ネパール語）...")
        result = translate_full_text(content)
        print("\n--- 翻訳結果 ---\n")
        print(result)
    except Exception as e:
        print(f"エラーが発生しました: {e}")
