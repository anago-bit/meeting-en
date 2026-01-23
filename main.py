import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google import genai

# --- 環境変数（GitHub Secretsから取得） ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SOURCE_FOLDER_ID = os.environ.get("SOURCE_FOLDER_ID") # Meet議事録の元フォルダID
TARGET_FOLDER_ID = os.environ.get("TARGET_FOLDER_ID") # 翻訳対象を隔離する専用フォルダID
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON")

# フィルタリングするキーワード（題名に含まれるべき文字列）
SEARCH_KEYWORD = "レンタカー/リース会議"

# 権限範囲（読み書き・移動が必要なため full drive/docs スコープ）
SCOPES = [
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive'
]

def get_credentials():
    """サービスアカウントの認証"""
    if not SERVICE_ACCOUNT_JSON:
        raise ValueError("環境変数 SERVICE_ACCOUNT_JSON が未設定です。")
    info = json.loads(SERVICE_ACCOUNT_JSON)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

def find_and_move_latest_meeting_doc():
    """全体フォルダから特定の名前の最新ファイルを探して専用フォルダへ移動"""
    creds = get_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    
    # 1. ソースフォルダ内からキーワードを含み、かつドキュメント形式のファイルを検索（更新順）
    query = f"'{SOURCE_FOLDER_ID}' in parents and name contains '{SEARCH_KEYWORD}' and mimeType = 'application/vnd.google-apps.document' and trashed = false"
    results = drive_service.files().list(
        q=query, 
        orderBy="modifiedTime desc", 
        pageSize=1, 
        fields="files(id, name, parents)"
    ).execute()
    
    files = results.get('files', [])
    
    if not files:
        print(f"情報: キーワード「{SEARCH_KEYWORD}」を含む新しい議事録は見つかりませんでした。")
        return None, None

    target_file = files[0]
    file_id = target_file['id']
    file_name = target_file['name']

    # 2. 専用フォルダへ移動（まだ移動していない場合のみ）
    if TARGET_FOLDER_ID not in target_file.get('parents', []):
        print(f"🔒 セキュリティ仕分け: 「{file_name}」を専用フォルダへ移動します。")
        previous_parents = ",".join(target_file.get('parents'))
        drive_service.files().update(
            fileId=file_id,
            addParents=TARGET_FOLDER_ID,
            removeParents=previous_parents,
            fields='id, parents'
        ).execute()
    
    return file_id, file_name

def read_doc(doc_id):
    """Googleドキュメントの内容を抽出"""
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
    """Geminiによる一字一句の翻訳"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    以下の議事録を、内容を省略したり要約したりせずに、原文のすべての発言を網羅して翻訳してください。
    出力は「英語」と「ネパール語」の両方で行ってください。

    【ルール】
    - 要約は一切禁止です。
    - 全ての発言を一字一句漏らさず翻訳してください。
    - 形式は、日本語原文に対応するように「英語：」「ネパール語：」と分けて記述してください。

    議事録内容:
    {text}
    """
    
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    return response.text

def create_translated_doc(folder_id, original_name, translated_text):
    """翻訳済みドキュメントを作成し専用フォルダに保存"""
    creds = get_credentials()
    docs_service = build('docs', 'v1', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)

    # ドキュメント作成
    title = f"【翻訳完了】{original_name}"
    doc = docs_service.documents().create(body={'title': title}).execute()
    doc_id = doc.get('documentId')

    # 書き込み
    requests = [{'insertText': {'location': {'index': 1}, 'text': translated_text}}]
    docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()

    # 専用フォルダへ移動
    file = drive_service.files().get(fileId=doc_id, fields='parents').execute()
    drive_service.files().update(
        fileId=doc_id, 
        addParents=folder_id, 
        removeParents=",".join(file.get('parents'))
    ).execute()
    
    return doc_id

if __name__ == "__main__":
    try:
        print(">>> 1. 議事録の検索とセキュリティ仕分けを開始...")
        target_id, target_name = find_and_move_latest_meeting_doc()
        
        if target_id:
            print(f">>> 2. 対象ファイル: {target_name}")
            content = read_doc(target_id)
            
            print(">>> 3. Geminiによる一字一句翻訳を実行中...")
            translated_result = translate_full_text(content)
            
            print(">>> 4. 翻訳済みドキュメントを作成中...")
            new_id = create_translated_doc(TARGET_FOLDER_ID, target_name, translated_result)
            
            print(f"\n✅ 成功！専用フォルダに保存されました。")
            print(f"URL: https://docs.google.com/document/d/{new_id}/edit")
        else:
            print("処理を終了します。")
        
    except Exception as e:
        print(f"❌ エラーが発生しました: {e}")
