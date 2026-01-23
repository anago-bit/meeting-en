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
    info = json.loads(SERVICE_ACCOUNT_JSON)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

def find_latest_doc():
    service = build('drive', 'v3', credentials=get_credentials())
    query = f"'{SOURCE_FOLDER_ID}' in parents and name contains '{SEARCH_KEYWORD}' and mimeType = 'application/vnd.google-apps.document' and trashed = false"
    results = service.files().list(q=query, orderBy="modifiedTime desc", pageSize=1, fields="files(id, name)", includeItemsFromAllDrives=True, supportsAllDrives=True).execute()
    files = results.get('files', [])
    return files[0] if files else None

def read_doc(doc_id):
    service = build('docs', 'v1', credentials=get_credentials())
    document = service.documents().get(documentId=doc_id).execute()
    text = ""
    for content in document.get('body').get('content'):
        if 'paragraph' in content:
            for element in content.get('paragraph').get('elements'):
                text += element.get('textRun', {}).get('content', '')
        elif 'table' in content:
            for row in content.get('table').get('tableRows'):
                for cell in row.get('tableCells'):
                    for cell_content in cell.get('content'):
                        if 'paragraph' in cell_content:
                            for element in cell_content.get('paragraph').get('elements'):
                                text += element.get('textRun', {}).get('content', '')
    return text

def translate_full_text(text):
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"以下の議事録を一字一句漏らさず英語とネパール語に翻訳してください。要約禁止。\n\n{text}"
    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
    return response.text

def create_translated_doc(original_name, translated_text):
    drive_service = build('drive', 'v3', credentials=get_credentials())
    docs_service = build('docs', 'v1', credentials=get_credentials())
    title = f"【翻訳完了】{original_name}"
    file_metadata = {'name': title, 'mimeType': 'application/vnd.google-apps.document', 'parents': [TARGET_FOLDER_ID]}
    file = drive_service.files().create(body=file_metadata, fields='id', supportsAllDrives=True).execute()
    doc_id = file.get('id')
    requests_list = [{'insertText': {'location': {'index': 1}, 'text': translated_text}}]
    docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests_list}).execute()
    return doc_id, title

def copy_original_file(file_id, original_name):
    """元のファイルをターゲットフォルダへコピーする"""
    drive_service = build('drive', 'v3', credentials=get_credentials())
    try:
        copy_metadata = {
            'name': f"【原本】{original_name}",
            'parents': [TARGET_FOLDER_ID]
        }
        drive_service.files().copy(
            fileId=file_id, 
            body=copy_metadata, 
            supportsAllDrives=True
        ).execute()
        print(f">>> 元ファイルのコピー完了")
    except Exception as e:
        print(f"⚠️ コピー失敗: {e}")

def post_to_talknote(title, doc_url):
    if not TALKNOTE_API_TOKEN or not TALKNOTE_GROUP_ID:
        print(f"Talknote設定が不足しています (TOKEN={bool(TALKNOTE_API_TOKEN)}, GROUP={bool(TALKNOTE_GROUP_ID)})")
        return
    headers = {"Authorization": f"Bearer {TALKNOTE_API_TOKEN}"}
    message = f"✅ 翻訳完了通知\n\n【件名】: {title}\n【URL】: {doc_url}"
    res = requests.post("https://api.talknote.com/v1/posts", headers=headers, data={"group_id": TALKNOTE_GROUP_ID, "body": message})
    print("✅ Talknote投稿成功" if res.status_code == 200 else f"❌ Talknote投稿失敗: {res.text}")

if __name__ == "__main__":
    try:
        target_file = find_latest_doc()
        if target_file:
            print(f">>> 処理開始: {target_file['name']}")
            content = read_doc(target_file['id'])
            print(f">>> 取得文字数: {len(content)} 文字")
            
            # 翻訳
            translated = translate_full_text(content)
            
            # 翻訳済みドキュメント作成
            new_id, new_title = create_translated_doc(target_file['name'], translated)
            
            # 元ファイルをコピーして保存 ★ここを移動からコピーに変更
            copy_original_file(target_file['id'], target_file['name'])
            
            url = f"https://docs.google.com/document/d/{new_id}/edit"
            print(f"✅ 全工程完了 URL: {url}")
            post_to_talknote(new_title, url)
        else:
            print("対象ファイルが見つかりませんでした。")
    except Exception as e:
        print(f"❌ 致命的エラー: {e}")
