import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google import genai

# --- 環境変数設定 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
FOLDER_ID = os.environ.get("FOLDER_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON")

# Google Drive API (ファイル検索) と Docs API (内容読み取り) のスコープ
SCOPES = [
    'https://www.googleapis.com/auth/documents.readonly',
    'https://www.googleapis.com/auth/drive.metadata.readonly'
]

def get_credentials():
    """サービスアカウントの認証情報を取得"""
    if not SERVICE_ACCOUNT_JSON:
        raise ValueError("環境変数 SERVICE_ACCOUNT_JSON が設定されていません。GitHub Secretsを確認してください。")
    info = json.loads(SERVICE_ACCOUNT_JSON)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

def get_latest_doc_id(folder_id):
    """指定されたフォルダ内で最後に更新されたGoogleドキュメントのIDを取得"""
    creds = get_credentials()
    service = build('drive', 'v3', credentials=creds)
    
    # フォルダ内のドキュメントを更新日時が新しい順に1件取得
    query = f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.document' and trashed = false"
    results = service.files().list(
        q=query,
        orderBy="modifiedTime desc", 
        pageSize=1,
        fields="files(id, name)"
    ).execute()
    
    files = results.get('files', [])
    if not files:
        raise Exception(f"フォルダ(ID: {folder_id})内にGoogleドキュメントが見つかりませんでした。")
    
    print(f"ターゲットファイルを確認: {files[0]['name']} (ID: {files[0]['id']})")
    return files[0]['id']

def read_doc(doc_id):
    """Googleドキュメントから全テキストを抽出"""
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
    """Geminiを使用して一字一句翻訳を実行"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # 指示：要約禁止、一字一句網羅
    prompt = f"""
    以下の議事録を、内容を省略したり要約したりせずに、原文のすべての発言を網羅して翻訳してください。
    出力は「英語」と「ネパール語」の両方で行ってください。

    【ルール】
    - 要約は一切禁止です。
    - 意訳しすぎず、全ての発言を一字一句漏らさず翻訳してください。
    - 文脈を変えず、原文に忠実に記述してください。
    - 形式は、日本語原文に対応するように「英語：」「ネパール語：」と分けて記述してください。

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
        if not GEMINI_API_KEY or not FOLDER_ID:
            print("エラー: GEMINI_API_KEY または FOLDER_ID が未設定です。")
        else:
            print(f"1. フォルダ(ID: {FOLDER_ID})から最新ファイルを検索中...")
            latest_id = get_latest_doc_id(FOLDER_ID)
            
            print("2. ドキュメント内容を読み取り中...")
            content = read_doc(latest_id)
            
            if not content.strip():
                print("ドキュメントが空です。処理を終了します。")
            else:
                print("3. Geminiで一字一句翻訳中（英語・ネパール語）...")
                result = translate_full_text(content)
                
                print("\n--- 翻訳完了 ---")
                print(result)
                
    except Exception as e:
        print(f"\nプログラム実行中にエラーが発生しました: {e}")
