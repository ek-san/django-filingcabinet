import contextlib
import glob
import io
import logging
import os
import shutil
import subprocess
import tempfile

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import wand
from wand.image import Image
from wand.color import Color

from PyPDF2 import PdfFileReader
from PyPDF2.utils import PdfReadError
from PIL import Image as PILImage

try:
    import tesserocr
except ImportError:
    tesserocr = None
try:
    import pdflib
except ImportError:
    pdflib = None

from .utils import chunks

logger = logging.getLogger(__name__)


OFFICE_FILETYPES = (
    'application/msexcel',
    'application/vnd.ms-excel',
    'application/msword',
    'application/vnd.msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'text/rtf',
    'application/rtf',
)
OFFICE_EXTENSIONS = (
    '.doc',
    '.docx',
    '.odt',
    '.rtf',
)


TESSERACT_LANGUAGE = {
    'en': 'eng',
    'de': 'deu'
}


class PDFException(Exception):
    def __init__(self, exc, reason):
        self.exc = exc
        self.reason = reason


def try_reading_pdf(pdf_file, password=None):
    try:
        pdf_reader = PdfFileReader(pdf_file, strict=False)
    except (PdfReadError, ValueError, OSError) as e:
        raise PDFException(e, 'rewrite')

    if pdf_reader.isEncrypted:
        raise PDFException(None, 'decrypt')

    try:
        # Try reading number of pages
        pdf_reader.getNumPages()
    except KeyError as e:  # catch KeyError '/Pages'
        raise PDFException(e, 'rewrite')
    except PdfReadError as e:
        raise PDFException(e, 'decrypt')
    return pdf_reader


def get_readable_pdf(pdf_file, copy_func, password=None):
    tries = 0
    filesize = os.path.getsize(pdf_file)
    # one minute + 5 seconds per megabyte timeout
    timeout = 60 + 5 * filesize / (1024 * 1024)
    while True:
        try:
            pdf_reader = try_reading_pdf(pdf_file, password=password)
            return pdf_file, pdf_reader
        except PDFException as e:
            if tries == 0 and copy_func:
                pdf_file = copy_func(pdf_file)
            tries += 1
            if tries > 2:
                raise Exception('PDF Redaction Error')
            if e.reason == 'rewrite':
                next_pdf_file = rewrite_pdf_in_place(
                    pdf_file, password=password,
                    timeout=timeout
                )
                if next_pdf_file is None:
                    next_pdf_file = rewrite_hard_pdf_in_place(
                        pdf_file, password=password,
                        timeout=timeout
                    )
            elif e.reason == 'decrypt':
                next_pdf_file = decrypt_pdf_in_place(
                    pdf_file, password=password,
                    timeout=timeout
                )
            if next_pdf_file is None:
                raise Exception('PDF Rewrite Error')
            pdf_file = next_pdf_file


class PDFProcessor(object):
    def __init__(self, filename, copy_func=None, language=None, config=None):
        filename, pdf_reader = get_readable_pdf(filename, copy_func)
        self.filename = filename
        self.pdf_reader = pdf_reader
        self.num_pages = self.pdf_reader.getNumPages()
        self.language = language
        self.config = config or {}

    def get_pdf_reader(self, filename):
        try:
            return PdfFileReader(filename)
        except (PdfReadError, ValueError, OSError) as e:
            logger.error('Could not read PDF %s', filename)
            pass
        pdf_file_name = rewrite_pdf_in_place(filename)
        return PdfFileReader(pdf_file_name)

    def get_meta(self):
        doc_info = self.pdf_reader.getDocumentInfo()
        return {
            'title': doc_info.title
        }

    def get_images(self, pages=None, resolution=300, chunk_size=20):
        white = wand.color.Color('#fff')
        if pages is None:
            pages = list(range(1, self.num_pages + 1))
        images = get_images_from_pdf_chunked(
            self.filename, pages,
            chunk_size, dpi=resolution
        )
        for page_number, image_filename in images:
            logger.info('Generated page %s: %s', page_number, image_filename)
            with Image(filename=image_filename, background=white) as img:
                yield page_number, img

    def get_text_for_page(self, page_no, image=None):
        text = self._get_text_for_page(page_no)
        if not text.strip() and image is not None:
            text = self.run_ocr_on_image(image)
        return text.strip()

    def _get_text_for_page(self, page_no):
        if not hasattr(self, 'pdflib_pages'):
            if pdflib is not None:
                pdflib_doc = pdflib.Document(self.filename)
                self.pdflib_pages = list(pdflib_doc)
        if hasattr(self, 'pdflib_pages'):
            page = self.pdflib_pages[page_no - 1]
            return ' '.join(page.lines).strip()
        page = self.pdf_reader.getPage(page_no - 1)
        return page.extractText()

    def get_text(self, pages=None):
        if pages is None:
            pages = range(self.num_pages)
        for page_no in pages:
            yield self.get_for_page(page_no)

    def run_ocr_on_image(self, image):
        if tesserocr is None:
            return ''
        img_blob = image.make_blob('RGB')
        pil_image = PILImage.frombytes('RGB', image.size, img_blob)
        return tesserocr.image_to_text(
            pil_image,
            lang=TESSERACT_LANGUAGE[self.language],
            path=self.config.get('TESSERACT_DATA_PATH', '')
        )


def draw_highlights(highlights):
    def apply_highlights(img):
        for highlight in highlights:
            crop = img[
                highlight['left']:highlight['left'] + highlight['width'],
                highlight['top']:highlight['top'] + highlight['height']
            ]
            crop.opaque_paint(
                target=Color('white'),
                fill=Color(highlight['color']),
                fuzz=crop.quantum_range * 0.3,
            )
            img.composite(
                crop, left=highlight['left'], top=highlight['top']
            )
    return apply_highlights


def crop_image(image_path, left, top, width, height, transform_func=None):
    with Image(filename=image_path) as img:
        img.alpha_channel = False
        img.crop(left, top, left + width, top + height)
        if transform_func is not None:
            transform_func(img)
        return img.make_blob('gif')


def can_convert_to_pdf(filetype, name=None):
    return filetype.lower() in OFFICE_FILETYPES or (
        name is not None and name.lower().endswith(OFFICE_EXTENSIONS))


def convert_to_pdf(filepath, binary_name=None, construct_call=None,
                   timeout=120):
    if binary_name is None and construct_call is None:
        return
    outpath = tempfile.mkdtemp()
    path, filename = os.path.split(filepath)
    parts = filename.rsplit('.', 1)
    name = parts[0]
    output_file = os.path.join(outpath, '%s.pdf' % name)
    arguments = [
        binary_name,
        '--headless',
        '--nodefault',
        '--nofirststartwizard',
        '--nolockcheck',
        '--nologo',
        '--norestore',
        '--invisible',
        '--convert-to',
        'pdf',
        '--outdir',
        outpath,
        filepath
    ]
    if construct_call is not None:
        arguments, output_file = construct_call(filepath, outpath)

    try:
        output_bytes = shell_call(
            arguments, outpath, output_file,
            timeout=timeout
        )
        return output_bytes
    except Exception as err:
        logger.error("Error during Doc to PDF conversion: %s", err)
        logger.exception(err)
    finally:
        shutil.rmtree(outpath)
    return None


def convert_images_to_ocred_pdf(filenames, language='en', instructions=None):
    try:
        temp_dir = tempfile.mkdtemp()
        output_file = os.path.join(temp_dir, 'out.pdf')
        pdf_bytes = convert_images_to_pdf(filenames, instructions=instructions)

        with open(output_file, 'wb') as f:
            f.write(pdf_bytes)

        return run_ocr(output_file, language=language, timeout=180)

    except Exception as err:
        logger.error("Error during convert images to ocred pdf: %s", err)
        logger.exception(err)
        return None
    finally:
        # Delete all temporary files
        shutil.rmtree(temp_dir)


def run_ocr(filename, language=None, binary_name='ocrmypdf', timeout=50):
    if binary_name is None:
        return
    outpath = tempfile.mkdtemp()
    lang = TESSERACT_LANGUAGE[language]
    output_file = os.path.join(outpath, 'out.pdf')
    arguments = [
        binary_name,
        '-l',
        lang,
        '--deskew',
        '--skip-text',
        # '--title', title
        filename,
        output_file
    ]
    try:
        output_bytes = shell_call(
            arguments, outpath, output_file,
            timeout=timeout
        )
        return output_bytes
    except Exception as err:
        logger.error("Error during PDF OCR: %s", err)
        logger.exception(err)
    finally:
        shutil.rmtree(outpath)
    return None


def shell_call(arguments, outpath, output_file=None, timeout=50):
    env = dict(os.environ)
    env.update({'HOME': outpath})

    logger.info("Running: %s", arguments)
    out, err = '', ''
    p = None
    try:
        p = subprocess.Popen(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env
        )

        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if p is not None:
            p.kill()
            out, err = p.communicate()
    finally:
        if p is not None and p.returncode is None:
            p.kill()
            out, err = p.communicate()
    if p is not None and p.returncode == 0:
        if output_file is not None and os.path.exists(output_file):
            with open(output_file, 'rb') as f:
                return f.read()
    if output_file is not None:
        raise Exception(err)


def run_command_overwrite(filename, argument_func, timeout=50):
    try:
        temp_dir = tempfile.mkdtemp()
        temp_out = os.path.join(temp_dir, 'gs_pdf_out.pdf')
        arguments, temp_out = argument_func(filename, temp_dir)
        output_bytes = shell_call(
            arguments, temp_dir, temp_out, timeout=timeout
        )

        with open(filename, 'wb') as f:
            f.write(output_bytes)
        return filename
    except Exception as err:
        logger.error("Error during command overwrite %s", err)
        logger.exception(err)
        return None
    finally:
        # Delete all temporary files
        shutil.rmtree(temp_dir)


def decrypt_pdf_in_place(filename, password=None, timeout=50):
    def argument_func(filename, temp_dir):
        temp_out = os.path.join(temp_dir, 'qpdf_out.pdf')
        arguments = ['qpdf', '--decrypt']

        if password is not None:
            arguments.extend([
                '--password=%s' % password
            ])

        arguments.extend([filename, temp_out])
        return arguments, temp_out

    return run_command_overwrite(filename, argument_func, timeout=timeout)


def rewrite_pdf_in_place(filename, password=None, timeout=50):
    def argument_func(filename, temp_dir):
        temp_out = os.path.join(temp_dir, 'gs_pdf_out.pdf')
        arguments = [
            'gs', '-o', temp_out,
        ]
        if password is not None:
            arguments.extend([
                '-sPDFPassword=%s' % password
            ])
        arguments.extend([
            '-sDEVICE=pdfwrite',
            '-dPDFSETTINGS=/prepress',
            filename
        ])
        return arguments, temp_out

    return run_command_overwrite(filename, argument_func, timeout=timeout)


def rewrite_hard_pdf_in_place(filename, password=None, timeout=50):
    def argument_func(filename, temp_dir):
        temp_out = os.path.join(temp_dir, 'pdfcairo_out.pdf')
        arguments = [
            'pdftocairo',
            '-pdf',
        ]
        if password is not None:
            arguments.extend([
                '-upw', password
            ])
        arguments.extend([
            filename,
            temp_out
        ])
        return arguments, temp_out

    return run_command_overwrite(filename, argument_func, timeout=timeout)


MAX_HEIGHT_A4 = 3507  # in pixels at 300 dpi


def convert_images_to_pdf(filenames, instructions=None, dpi=300):
    if instructions is None:
        instructions = [{} for _ in range(len(filenames))]
    a4_width, a4_height = A4
    writer = io.BytesIO()
    pdf = canvas.Canvas(writer, pagesize=A4)
    for filename, instruction in zip(filenames, instructions):
        with Image(filename=filename, resolution=dpi) as image:
            image.background_color = Color('white')
            image.format = 'jpg'
            image.alpha_channel = 'remove'
            try:
                degree = instruction.get('rotate', 0)
                if degree and degree % 90 == 0:
                    image.rotate(degree)
            except ValueError:
                pass

            if image.width > image.height:
                ratio = MAX_HEIGHT_A4 / image.width
            else:
                ratio = MAX_HEIGHT_A4 / image.height
            if ratio < 1:
                image.resize(
                    round(ratio * image.width),
                    round(ratio * image.height)
                )

            width = image.width * 72 / dpi
            height = image.height * 72 / dpi
            pdf.setPageSize((width, height))
            reportlab_io_img = ImageReader(io.BytesIO(image.make_blob()))
            pdf.drawImage(reportlab_io_img, 0, 0, width=width, height=height)
            pdf.showPage()
    pdf.save()
    return writer.getvalue()


def get_images_from_pdf_chunked(filename, pages, chunk_size, dpi=300):
    for pages in chunks(pages, chunk_size):
        with get_images_from_pdf(
                filename, pages=pages, dpi=dpi) as images:
            yield from images


@contextlib.contextmanager
def get_images_from_pdf(filename, pages=None, dpi=300, timeout=5 * 60):
    try:
        temp_dir = tempfile.mkdtemp()
        yield run_pdfto_ppm_on_pages(filename, temp_dir, pages, dpi, timeout)
    except Exception as err:
        logger.error("Error during command overwrite %s", err)
        logger.exception(err)
    finally:
        # Delete all temporary files
        shutil.rmtree(temp_dir)


def run_pdfto_ppm_on_pages(filename, temp_dir, pages, dpi, timeout):
    temp_out = os.path.join(temp_dir, 'image')

    base_arguments = [
        'pdftoppm', '-png', '-r', str(dpi),
    ]

    if pages is not None:
        pages = list(pages)
        pages.sort()
        page_iterator = get_continuous_pages(pages)
    else:
        page_iterator = ((None, None) for _ in (None,))

    for first, last in page_iterator:
        arguments = list(base_arguments)
        if first is not None:
            arguments.extend(['-f', str(first), '-l', str(last)])

        arguments.extend([filename, temp_out])
        shell_call(
            arguments, temp_dir, output_file=None, timeout=timeout
        )

    images = glob.glob(temp_out + '-*.png')
    images.sort()
    return [
        (get_page_number(filename), filename) for filename in images
    ]


def get_page_number(filename):
    return int(filename.rsplit('-', 1)[1].split('.')[0])


def get_continuous_pages(pages):
    first, last = None, None

    for page in pages:
        if first is None:
            first = page
            last = page
            continue
        if page - last > 1:
            yield (first, last)
            first = page
            last = page
            continue
        last = page
    yield (first, last)
