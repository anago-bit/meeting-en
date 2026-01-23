import os
import json
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google import genai

# --- 環境変数 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SOURCE_FOLDER_ID = os.environ.get("SOURCE_FOLDER_ID")
TARGET_FOLDER_ID = os.environ.get("TARGET_FOLDER_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON")
SEARCH_KEYWORD = os.environ.get("SEARCH_KEYWORD")
TALKNOTE_API_TOKEN = os.environ.get("TALKNOTE_API_TOKEN")
TALKNOTE_GROUP_ID = os.environ.get("TALKNOTE_GROUP_ID")

SCOPES = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']

def get_credentials():
    if not SERVICE_ACCOUNT_JSON:
        raise ValueError("環境変数 SERVICE_ACCOUNT_JSON が未設定です。")
    info = json.loads(SERVICE_ACCOUNT_JSON)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

def find_and_move_latest_meeting_doc():
    """SOURCEフォルダからキーワードに合う最新ファイルを探して移動。移動済みならそのまま。"""
    creds = get_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    
    # 検索範囲を SOURCE または TARGET の両方にする（移動済みでも見つけられるように）
    query = (
        f"( '{SOURCE_FOLDER_ID}' in parents or '{TARGET_FOLDER_ID}' in parents ) and "
        f"name contains '{SEARCH_KEYWORD}' and "
        f"mimeType = 'application/vnd.google-apps.document' and "
        f"trashed = false"
    )
    
    results = drive_service.files().list(q=query, orderBy="modifiedTime desc", pageSize=1, fields="files(id, name, parents)").execute()
    files = results.get('files', [])
    
    if not files:
        print(f"情報: 「{SEARCH_KEYWORD}」を含むファイルは見つかりませんでした。")
        return None, None

    target_file = files[0]
    file_id = target_file['id']
    file_name = target_file['name']
    current_parents = target_file.get('parents', [])

    # すでに TARGET フォルダにいる場合は移動処理をスキップ
    if TARGET_FOLDER_ID in current_parents:
        print(f"✅ すでに専用フォルダに存在します: 「{file_name}」")
    else:
        print(f"🔒 隔離移動を実行中: 「{file_name}」")
        previous_parents = ",".join(current_parents)
        try:
            drive_service.files().update(
                fileId=file_id, 
                addParents=TARGET_FOLDER_ID, 
                removeParents=previous_parents
            ).execute()
        except Exception as e:
            print(f"移動処理中にエラー(404等)が発生しましたが、ファイルは存在するため続行します。")
    
    return file_id, file_name

def read_doc(doc_id):
    creds = get_credentials()
    service = build('docs', 'v1', credentials=creds)
    document = service.documents().get(documentId=doc_id).execute()
    full_text = []
    def extract_text_elements(elements):
        text = ""
        for element in elements:
            if 'textRun' in element:
                text += element.get('textRun').get('content', '')
        return text
    for content in document.get('body').get('content'):
        if 'paragraph' in content:
            full_text.append(extract_text_elements(content.get('paragraph').get('elements')))
        elif 'table' in content:
            for row in content.get('table').get('tableRows'):
                for cell in row.get('tableCells'):
                    for cell_content in cell.get('content'):
                        if 'paragraph' in cell_content:
                            full_text.append(extract_text_elements(cell_content.get('paragraph').get('elements')))
    return "\n".join(full_text)

def translate_full_text(text):
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"以下の議事録を省略せず一字一句翻訳してください。英語とネパール語で出力してください。\n\n議事録テキスト:\n{text}"
    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
    return response.text

def create_translated_doc(folder_id, original_name, translated_text):
    """最初からTARGETフォルダ内にドキュメントを作成"""
    creds = get_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    docs_service = build('docs', 'v1', credentials=creds)

    title = f"【翻訳完了】{original_name}"
    file_metadata = {
        'name': title,
        'mimeType': 'application/vnd.google-apps.document',
        'parents': [folder_id]
    }
    file = drive_service.files().create(body=file_metadata, fields='id').execute()
    doc_id = file.get('id')

    requests = [{'insertText': {'location': {'index': 1}, 'text': translated_text}}]
    docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()
    return doc_id, title

def post_to_talknote(title, doc_url):
    if not TALKNOTE_API_TOKEN or not TALKNOTE_GROUP_ID:
        print("Talknote設定がありません。投稿をスキップします。")
        return
    url = "https://api.talknote.com/v1/posts"
    headers = {"Authorization": f"Bearer {TALKNOTE_API_TOKEN}"}
    message = f"📢 翻訳完了通知\n\n【件名】: {title}\n【URL】: {doc_url}"
    data = {"group_id": TALKNOTE_GROUP_ID, "body": message}
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        print("✅ Talknote投稿成功")
    else:
        print(f"❌ Talknote投稿エラー: {response.text}")

if __name__ == "__main__":
    try:
        fid, fname = find_and_move_latest_meeting_doc()
        if fid:
            print(f">>> 読み取り中: {fname}")
            content = read_doc(fid)
            print(f"取得文字数: {len(content)} 文字")
            print(">>> 翻訳中...")
            result = translate_full_text(content)
            print(">>> 保存中...")
            new_id, new_title = create_translated_doc(TARGET_FOLDER_ID, fname, result)
            new_url = f"https://docs.google.com/document/d/{new_id}/edit"
            print(f"✅ 成功！ URL: {new_url}")
            post_to_talknote(new_title, new_url)
    except Exception as e:
        print(f"❌ エラー: {e}")
