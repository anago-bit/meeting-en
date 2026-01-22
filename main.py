import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google import genai  # 最新ライブラリ

# --- 設定 ---
GEMINI_API_KEY = "AIzaSyBZxMEAIIDVwZqX_qbJn0VaLwd6Mhw74ao"
DOC_ID = "1smGue8aGWySpU4BNUXeF9oPdWJt5ZsuJBd-Fv6-7YDY" # あなたのドキュメントID
SCOPES = ['https://www.googleapis.com/auth/documents.readonly']

def get_credentials():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds

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

def translate_and_summarize(text):
    # 最新のクライアント初期化方法
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
    
    # モデルの呼び出し
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    return response.text

if __name__ == "__main__":
    try:
        print("1. ドキュメントを読み込んでいます...")
        content = read_doc(DOC_ID)
        
        if not content.strip():
            print("ドキュメントが空、または内容を取得できませんでした。")
        else:
            print("2. Geminiで翻訳中...")
            result = translate_and_summarize(content)
            print("\n--- 翻訳・要約結果 ---\n")
            print(result)
            
    except Exception as e:
        print(f"\nエラーが発生しました: {e}")
