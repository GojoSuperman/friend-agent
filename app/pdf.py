"""PDF 공지문 글자 읽기. 공지 PDF를 보내면 글자를 꺼내 대화 입력창에 넣어 준다.

글자를 꺼내는 pdftotext 는 Python 라이브러리가 아니라 운영체제에 따로 설치하는 프로그램이다(poppler-utils).
노트북에는 직접 설치하고, 서버에서는 실행 환경(이미지)에 들어 있어야 한다.
"""
import subprocess
import tempfile

from app.logs import log

MAX_BYTES = 5 * 1024 * 1024  # PDF 한 개 최대 5MB


class PdfError(Exception):
    pass


def read_text(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise PdfError("PDF가 5MB보다 큽니다.")
    if not data.startswith(b"%PDF"):
        raise PdfError("PDF 파일이 아닙니다.")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as file:
        file.write(data)
        file.flush()
        try:
            # -layout: 줄 모양을 PDF와 비슷하게 유지한다. 마지막 "-" 는 결과를 파일 대신 화면(stdout)으로 내보낸다.
            result = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", file.name, "-"],
                                    capture_output=True, timeout=20)
        except FileNotFoundError as error:
            log("ERROR", "pdftotext 프로그램을 찾을 수 없습니다. 실행 환경에 poppler-utils 가 설치되어 있지 않습니다.")
            raise PdfError("PDF에서 글자를 읽는 프로그램이 서버에 없습니다.") from error
        except subprocess.TimeoutExpired as error:
            raise PdfError("PDF를 읽는 데 너무 오래 걸립니다.") from error
    if result.returncode != 0:
        log("WARNING", "PDF 글자 읽기 실패", code=result.returncode)
        raise PdfError("PDF 파일을 읽을 수 없습니다.")
    text = result.stdout.decode("utf-8", errors="replace")
    text = "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())
    log("INFO", "PDF 글자 읽기", chars=len(text))
    return text
