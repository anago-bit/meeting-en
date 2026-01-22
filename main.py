import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google import genai

# --- 環境変数から取得 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
FOLDER_ID = os.environ.get("FOLDER_ID") # Meet議事録が保存されるフォルダID
SA_JSON_STR = os.environ.get("SERVICE_ACCOUNT_JSON")

def get_credentials():
    if SA_JSON_STR:
        info = json.loads(SA_JSON_STR)
        return service_account.Credentials.from_service_account_info(info)
    raise Exception("SERVICE_ACCOUNT_JSON が設定されていません。")

def get_latest_doc_id(folder_id):
    """指定したフォルダ内の最新ドキュメントIDを取得する"""
    creds = get_credentials()
    service = build('drive', 'v3', credentials=creds)
    
    # フォルダ内のドキュメントを、作成日時が新しい順に1件取得
    query = f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.document'"
    results = service.files().list(
        q=query,
        orderBy="createdTime desc",
        pageSize=1,
        fields="files(id, name, createdTime)"
    ).execute()
    
    files = results.get('files', [])
    if not files:
        return None, None
    return files[0]['id'], files[0]['name']

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
    
    議事録内容:
    {text}
    """
    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
    return response.text

if __name__ == "__main__":
    try:
        print(f"フォルダID: {FOLDER_ID} 内をスキャン中...")
        doc_id, doc_name = get_latest_doc_id(FOLDER_ID)
        
        if not doc_id:
            print("新しい議事録が見つかりませんでした。")
        else:
            print(f"最新の議事録 '{doc_name}' を読み込んでいます...")
            content = read_doc(doc_id)
            print("Geminiで翻訳中...")
            result = translate_full_text(content)
            print("\n--- 翻訳結果 ---\n")
            print(result)
            
            # ここにTalknoteへの投稿コードを追加する
            
    except Exception as e:
        print(f"エラーが発生しました: {e}")
