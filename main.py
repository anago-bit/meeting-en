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
    """SOURCEまたはTARGETから最新ファイルを探す"""
    creds = get_credentials()
    service = build('drive', 'v3', credentials=creds)
    query = f"( '{SOURCE_FOLDER_ID}' in parents or '{TARGET_FOLDER_ID}' in parents ) and name contains '{SEARCH_KEYWORD}' and mimeType = 'application/vnd.google-apps.document' and trashed = false"
    results = service.files().list(q=query, orderBy="modifiedTime desc", pageSize=1, fields="files(id, name)").execute()
    files = results.get('files', [])
    return files[0] if files else (None, None)

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

def create_and_move_doc(original_name, translated_text):
    """Drive APIを優先して使用する作成フロー"""
    creds = get_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    docs_service = build('docs', 'v1', credentials=creds)

    title = f"【翻訳完了】{original_name}"
    
    # 1. Drive APIを使ってドキュメントを作成（こちらの方が権限エラーに強い傾向があります）
    file_metadata = {
        'name': title,
        'mimeType': 'application/vnd.google-apps.document'
    }
    
    print(">>> ドキュメントの枠を作成中...")
    file = drive_service.files().create(body=file_metadata, fields='id').execute()
    doc_id = file.get('id')

    # 2. 本文を書き込み
    print(f">>> 内容を書き込み中... (ID: {doc_id})")
    requests = [{'insertText': {'location': {'index': 1}, 'text': translated_text}}]
    docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()

    # 3. ターゲットフォルダへ移動を試みる
    if TARGET_FOLDER_ID:
        try:
            # 現在の親フォルダ（通常はroot）を確認
            file_info = drive_service.files().get(fileId=doc_id, fields='parents').execute()
            previous_parents = ",".join(file_info.get('parents', []))
            
            drive_service.files().update(
                fileId=doc_id,
                addParents=TARGET_FOLDER_ID,
                removeParents=previous_parents,
                fields='id, parents'
            ).execute()
            print(f">>> 共有フォルダへの移動に成功しました。")
        except Exception as e:
            print(f"⚠️ 移動に失敗しました。マイドライブを確認してください。: {e}")

    return doc_id, title

    # 3. ターゲットフォルダへ移動を試みる
    try:
        file = drive_service.files().get(fileId=doc_id, fields='parents').execute()
        previous_parents = ",".join(file.get('parents'))
        drive_service.files().update(
            fileId=doc_id,
            addParents=TARGET_FOLDER_ID,
            removeParents=previous_parents,
            fields='id, parents'
        ).execute()
        print(f">>> 共有フォルダ(ID: {TARGET_FOLDER_ID})への移動に成功しました。")
    except Exception as e:
        print(f"⚠️ 共有フォルダへの移動に失敗しました。ファイルはマイドライブ直下に残っています。エラー: {e}")

    return doc_id, title

def post_to_talknote(title, doc_url):
    if not TALKNOTE_API_TOKEN: return
    message = f"📢 翻訳完了通知\n\n【件名】: {title}\n【URL】: {doc_url}"
    res = requests.post("https://api.talknote.com/v1/posts", 
                        headers={"Authorization": f"Bearer {TALKNOTE_API_TOKEN}"},
                        data={"group_id": TALKNOTE_GROUP_ID, "body": message})
    print("✅ Talknote投稿成功" if res.status_code == 200 else f"❌ Talknote投稿失敗: {res.text}")

if __name__ == "__main__":
    try:
        target_file = find_latest_doc()
        if target_file and 'id' in target_file:
            print(f">>> 処理開始: {target_file['name']}")
            content = read_doc(target_file['id'])
            print(f">>> 取得文字数: {len(content)} 文字")
            translated = translate_full_text(content)
            new_id, new_title = create_and_move_doc(target_file['name'], translated)
            url = f"https://docs.google.com/document/d/{new_id}/edit"
            print(f"✅ 完了 URL: {url}")
            post_to_talknote(new_title, url)
    except Exception as e:
        print(f"❌ 致命的エラー: {e}")
