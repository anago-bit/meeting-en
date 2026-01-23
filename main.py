import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google import genai

# --- 環境変数 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SOURCE_FOLDER_ID = os.environ.get("SOURCE_FOLDER_ID")
TARGET_FOLDER_ID = os.environ.get("TARGET_FOLDER_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON")
SEARCH_KEYWORD = os.environ.get("SEARCH_KEYWORD")

SCOPES = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']

def get_credentials():
    if not SERVICE_ACCOUNT_JSON:
        raise ValueError("環境変数 SERVICE_ACCOUNT_JSON が未設定です。")
    info = json.loads(SERVICE_ACCOUNT_JSON)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

def find_and_move_latest_meeting_doc():
    """SOURCEからSEARCH_KEYWORDを含む最新ファイルを探し、TARGETへ移動"""
    creds = get_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    
    query = (
        f"'{SOURCE_FOLDER_ID}' in parents and "
        f"name contains '{SEARCH_KEYWORD}' and "
        f"mimeType = 'application/vnd.google-apps.document' and "
        f"trashed = false"
    )
    
    results = drive_service.files().list(q=query, orderBy="modifiedTime desc", pageSize=1, fields="files(id, name, parents)").execute()
    files = results.get('files', [])
    
    if not files:
        print(f"情報: 「{SEARCH_KEYWORD}」を含む新しいファイルは見つかりませんでした。")
        return None, None

    target_file = files[0]
    file_id = target_file['id']
    file_name = target_file['name']

    if TARGET_FOLDER_ID not in target_file.get('parents', []):
        print(f"🔒 隔離移動中: 「{file_name}」")
        previous_parents = ",".join(target_file.get('parents'))
        drive_service.files().update(fileId=file_id, addParents=TARGET_FOLDER_ID, removeParents=previous_parents).execute()
    
    return file_id, file_name

def read_doc(doc_id):
    """Googleドキュメントの本文＋表（文字起こし）を抽出"""
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

    # ドキュメント構造の解析
    for content in document.get('body').get('content'):
        # 通常の段落
        if 'paragraph' in content:
            full_text.append(extract_text_elements(content.get('paragraph').get('elements')))
        
        # 文字起こしが格納されやすい「表（テーブル）」構造
        elif 'table' in content:
            for row in content.get('table').get('tableRows'):
                for cell in row.get('tableCells'):
                    for cell_content in cell.get('content'):
                        if 'paragraph' in cell_content:
                            full_text.append(extract_text_elements(cell_content.get('paragraph').get('elements')))

    return "\n".join(full_text)

def translate_full_text(text):
    """Geminiで一字一句翻訳（システムメッセージ無視の指示付）"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    送付するテキストはGoogle Meetの議事録です。
    
    【厳守事項】
    1. 冒頭の「要約が生成されませんでした」等のGoogleのシステムメッセージは翻訳対象外として無視してください。
    2. 実際の「発言者名」と「会話内容」をすべて探し、要約・省略せず一字一句すべて翻訳してください。
    3. 英語とネパール語の2言語で出力してください。

    議事録テキスト:
    {text}
    """
    
    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
    return response.text

def create_translated_doc(folder_id, original_name, translated_text):
    creds = get_credentials()
    docs_service = build('docs', 'v1', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)
    title = f"【翻訳完了】{original_name}"
    doc = docs_service.documents().create(body={'title': title}).execute()
    doc_id = doc.get('documentId')
    requests = [{'insertText': {'location': {'index': 1}, 'text': translated_text}}]
    docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()
    
    # ターゲットフォルダへ移動
    drive_service.files().update(fileId=doc_id, addParents=folder_id, removeParents="").execute()
    return doc_id

if __name__ == "__main__":
    try:
        id, name = find_and_move_latest_meeting_doc()
        if id:
            print(f">>> 読み取り中: {name}")
            content = read_doc(id)
            # デバッグ用：読み取った文字数を確認
            print(f"取得文字数: {len(content)} 文字")
            
            print(">>> 翻訳中...")
            result = translate_full_text(content)
            
            print(">>> 保存中...")
            new_doc_id = create_translated_doc(TARGET_FOLDER_ID, name, result)
            print(f"✅ 成功: https://docs.google.com/document/d/{new_doc_id}/edit")
    except Exception as e:
        print(f"❌ エラー: {e}")
