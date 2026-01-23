import os
import json
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google import genai

# --- 環境変数（GitHub Secretsから取得） ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SOURCE_FOLDER_ID = os.environ.get("SOURCE_FOLDER_ID")
TARGET_FOLDER_ID = os.environ.get("TARGET_FOLDER_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON")
SEARCH_KEYWORD = os.environ.get("SEARCH_KEYWORD")
TALKNOTE_API_TOKEN = os.environ.get("TALKNOTE_API_TOKEN")
TALKNOTE_GROUP_ID = os.environ.get("TALKNOTE_GROUP_ID")

# 必要な権限範囲
SCOPES = [
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive'
]

def get_credentials():
    """サービスアカウントの認証情報を取得"""
    info = json.loads(SERVICE_ACCOUNT_JSON)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

def find_latest_doc():
    """SOURCEフォルダ（マイドライブ等）からキーワードに合う最新ドキュメントを探す"""
    creds = get_credentials()
    # 共有ドライブ対応のため v3 を使用
    service = build('drive', 'v3', credentials=creds)
    
    # マイドライブと共有ドライブの両方を検索対象にする
    query = (
        f"'{SOURCE_FOLDER_ID}' in parents and "
        f"name contains '{SEARCH_KEYWORD}' and "
        f"mimeType = 'application/vnd.google-apps.document' and "
        f"trashed = false"
    )
    
    results = service.files().list(
        q=query, 
        orderBy="modifiedTime desc", 
        pageSize=1, 
        fields="files(id, name, parents)",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True
    ).execute()
    
    files = results.get('files', [])
    return files[0] if files else None

def read_doc(doc_id):
    """Googleドキュメントの本文（段落と表）を読み取る"""
    service = build('docs', 'v1', credentials=get_credentials())
    document = service.documents().get(documentId=doc_id).execute()
    text = ""
    
    def extract_from_elements(elements):
        res = ""
        for element in elements:
            if 'textRun' in element:
                res += element.get('textRun', {}).get('content', '')
        return res

    for content in document.get('body').get('content'):
        if 'paragraph' in content:
            text += extract_from_elements(content.get('paragraph').get('elements'))
        elif 'table' in content:
            for row in content.get('table').get('tableRows'):
                for cell in row.get('tableCells'):
                    for cell_content in cell.get('content'):
                        if 'paragraph' in cell_content:
                            text += extract_from_elements(cell_content.get('paragraph').get('elements'))
    return text

def translate_full_text(text):
    """Geminiで翻訳（一字一句、要約禁止）"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""以下の会議議事録を、省略や要約を一切せずに、「英語」と「ネパール語」で翻訳してください。
全ての発言を一字一句漏らさず翻訳することがルールです。

原文:
{text}
"""
    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
    return response.text

def create_translated_doc(original_name, translated_text):
    """翻訳済みドキュメントをターゲットフォルダ内に直接作成"""
    creds = get_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    docs_service = build('docs', 'v1', credentials=creds)

    title = f"【翻訳完了】{original_name}"
    file_metadata = {
        'name': title,
        'mimeType': 'application/vnd.google-apps.document',
        'parents': [TARGET_FOLDER_ID]
    }
    
    # 共有ドライブの容量を使用して作成
    file = drive_service.files().create(
        body=file_metadata, 
        fields='id',
        supportsAllDrives=True
    ).execute()
    doc_id = file.get('id')

    # 本文の書き込み
    requests_list = [{'insertText': {'location': {'index': 1}, 'text': translated_text}}]
    docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests_list}).execute()
    
    return doc_id, title

def move_original_file(file_id):
    """元のファイルをターゲットフォルダへ移動させる"""
    creds = get_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    
    try:
        # 現在の親フォルダを取得
        file = drive_service.files().get(
            fileId=file_id, 
            fields='parents',
            supportsAllDrives=True
        ).execute()
        previous_parents = ",".join(file.get('parents', []))
        
        # 移動実行
        drive_service.files().update(
            fileId=file_id,
            addParents=TARGET_FOLDER_ID,
            removeParents=previous_parents,
            supportsAllDrives=True
        ).execute()
        print(f">>> 元ファイル(ID: {file_id})の移動が完了しました。")
    except Exception as e:
        print(f"⚠️ 元ファイルの移動に失敗しました（権限不足の可能性があります）: {e}")

def post_to_talknote(title, doc_url):
    """Talknoteへ通知を投稿"""
    if not TALKNOTE_API_TOKEN or not TALKNOTE_GROUP_ID:
        print("Talknote設定が不足しています。")
        return
    
    message = f"✅ 議事録の自動翻訳・整理が完了しました。\n\n【タイトル】\n{title}\n\n【ドキュメントURL】\n{doc_url}"
    headers = {"Authorization": f"Bearer {TALKNOTE_API_TOKEN}"}
    payload = {"group_id": TALKNOTE_GROUP_ID, "body": message}
    
    res = requests.post("https://api.talknote.com/v1/posts", headers=headers, data=payload)
    if res.status_code == 200:
        print("✅ Talknote通知に成功しました。")
    else:
        print(f"❌ Talknote投稿失敗: {res.text}")

if __name__ == "__main__":
    try:
        # 1. 検索
        target_file = find_latest_doc()
        if target_file:
            orig_id = target_file['id']
            orig_name = target_file['name']
            print(f">>> 処理開始: {orig_name}")
            
            # 2. 読み取り
            content = read_doc(orig_id)
            print(f">>> 取得文字数: {len(content)} 文字")
            
            # 3. 翻訳
            print(">>> Geminiによる翻訳を実行中...")
            translated = translate_full_text(content)
            
            # 4. 翻訳ドキュメント作成
            print(">>> 翻訳ドキュメントを保存中...")
            new_id, new_title = create_translated_doc(orig_name, translated)
            
            # 5. 元ファイルを移動
            print(">>> 元ファイルをターゲットフォルダへ移動中...")
            move_original_file(orig_id)
            
            url = f"https://docs.google.com/document/d/{new_id}/edit"
            print(f"✅ 全工程が完了しました！ URL: {url}")
            
            # 6. 通知
            post_to_talknote(new_title, url)
        else:
            print(f"情報: キーワード「{SEARCH_KEYWORD}」を含む新しいファイルは見つかりませんでした。")
            
    except Exception as e:
        print(f"❌ 致命的エラーが発生しました: {e}")
