import os
import re
import zipfile
import platform
from flask import Flask, request, render_template, send_file, jsonify, redirect, url_for
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytesseract
from pdf2image import convert_from_path
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

# ── Rutas según sistema operativo ──
if platform.system() == 'Windows':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    POPPLER_PATH = r'C:\poppler-25.12.0\Library\bin'
else:
    POPPLER_PATH = None  # En Linux/Render ya está instalado globalmente

app = Flask(__name__)
app.config['UPLOAD_FOLDER']    = 'uploads'
app.config['PROCESSED_FOLDER'] = 'processed'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB

ALLOWED_EXTENSIONS = {'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.errorhandler(RequestEntityTooLarge)
def archivo_muy_grande(e):
    return jsonify({"error": "Los archivos superan el límite. Súbelos en lotes más pequeños."}), 413

def clean_name(nombre_limpio, categoria_limpia):
    nombre_limpio    = re.sub(r'[^\w\s]', '', nombre_limpio,    flags=re.UNICODE).strip()
    nombre_limpio    = re.sub(r'\s+', ' ', nombre_limpio)
    categoria_limpia = re.sub(r'[^\w\s]', '', categoria_limpia, flags=re.UNICODE).strip()
    categoria_limpia = re.sub(r'\s+', '_', categoria_limpia)
    return f"{nombre_limpio}_CONTRATACION_POR_{categoria_limpia}"

def extract_name_from_text(text):
    if not text:
        return None

    asunto_match = re.search(
        r'ASUNTO\s*:\s*(.+?)(?:REFERENCIA|$)', text, re.DOTALL | re.IGNORECASE
    )
    if not asunto_match:
        return None

    bloque = asunto_match.group(1).strip()
    bloque = re.sub(r'\s+', ' ', bloque)
    bloque = re.sub(r'\s*[–—]\s*', ' ', bloque)

    print(f"\n=== BLOQUE ASUNTO ===\n{bloque}\n====================\n")

    # Patrón 1: categoría termina en punto antes del nombre
    patron = re.search(
        r'CONTRATACI[OÓ]N\s+POR\s+(.+?)\.\s*([A-ZÁÉÍÓÚÑ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ\w]{2,})+)\s*(?:\.|$)',
        bloque, re.IGNORECASE
    )
    if patron:
        categoria = patron.group(1).strip()
        nombre    = patron.group(2).strip()
        print(f"[P1] Categoría: {categoria} | Nombre: {nombre}")
        return clean_name(nombre, categoria)

    # Patrón 2: sin punto, nombre al final del bloque
    patron2 = re.search(
        r'CONTRATACI[OÓ]N\s+POR\s+(.+?)\s{2,}([A-ZÁÉÍÓÚÑ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ]{2,}){1,5})\s*$',
        bloque, re.IGNORECASE
    )
    if patron2:
        categoria = patron2.group(1).strip()
        nombre    = patron2.group(2).strip()
        print(f"[P2] Categoría: {categoria} | Nombre: {nombre}")
        return clean_name(nombre, categoria)

    # Patrón 3: líneas originales del texto
    lineas_originales = [l.strip() for l in text.split('\n') if l.strip()]
    idx_contratacion  = None
    for i, linea in enumerate(lineas_originales):
        if re.search(r'CONTRATACI[OÓ]N\s+POR', linea, re.IGNORECASE):
            idx_contratacion = i
            break

    if idx_contratacion is not None:
        palabras_excluir = [
            'ASUNTO', 'INFORME', 'REFERENCIA', 'GOBIERNO', 'REGIONAL',
            'MINEDU', 'UGEL', 'DOCENTE', 'RESULTADOS', 'SITUACIONES',
            'DIFERENCIADAS', 'CETPRO', 'PRIMARIA', 'SECUNDARIA', 'INICIAL',
            'EDUCACION', 'FISICA', 'NIVEL', 'PUN', 'EXPEDIENTES',
            'EVALUACION', 'MERITOS', 'CONTRATO', 'PLAZA', 'CARGO',
            'MODALIDAD', 'ESPECIAL', 'REGULAR', 'BASICA', 'TECNICA',
            'PRODUCTIVA', 'SUPERIOR', 'UNIVERSITARIA',
        ]
        categoria_lineas = []
        nombre = None

        linea_contratacion = lineas_originales[idx_contratacion]
        resto = re.sub(r'.*CONTRATACI[OÓ]N\s+POR\s*', '', linea_contratacion, flags=re.IGNORECASE).strip()
        resto = re.sub(r'\s*[–—-]\s*', ' ', resto)
        if resto:
            categoria_lineas.append(resto)

        for j in range(idx_contratacion + 1, min(idx_contratacion + 6, len(lineas_originales))):
            linea = lineas_originales[j].strip()
            linea = re.sub(r'\s*[–—-]\s*', ' ', linea)

            if re.search(r'REFERENCIA', linea, re.IGNORECASE):
                break

            palabras_linea = linea.upper().split()
            es_categoria   = any(w in palabras_excluir for w in palabras_linea)
            es_nombre      = (
                re.match(r'^[A-ZÁÉÍÓÚÑ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ]{2,})+\.?$', linea)
                and not es_categoria
            )

            if es_nombre:
                nombre = linea.rstrip('.')
                break
            else:
                categoria_lineas.append(linea)

        if nombre and categoria_lineas:
            categoria = ' '.join(categoria_lineas).strip()
            categoria = re.sub(r'\s*[–—-]\s*', ' ', categoria)
            print(f"[P3] Categoría: {categoria} | Nombre: {nombre}")
            return clean_name(nombre, categoria)

    print("[!] No se encontró nombre.")
    return None

def process_pdf(filepath, original_filename):
    result = {
        "original":       original_filename,
        "detected_name":  None,
        "final_filename": None,
        "status":         "error",
        "message":        "",
        "text_preview":   ""
    }
    try:
        images = convert_from_path(
            filepath, dpi=200, first_page=1, last_page=3,
            poppler_path=POPPLER_PATH
        )
        full_text = ""
        for img in images:
            page_text = pytesseract.image_to_string(img, lang='spa+eng', config='--psm 3')
            full_text += page_text + "\n"

        result["text_preview"] = full_text[:500].replace('\n', ' ')

        print(f"\n>>> Procesando: {original_filename}")
        name = extract_name_from_text(full_text)
        if not name:
            name = "Sin_Nombre_" + os.path.splitext(original_filename)[0]

        result["detected_name"] = name
        result["status"]        = "success"
        result["message"]       = "OCR completado"

    except Exception as e:
        result["message"]       = f"Error: {str(e)}"
        result["detected_name"] = "Error_" + os.path.splitext(original_filename)[0]

    return result

def get_unique_filename(base_name, processed_folder, used_names):
    candidate = base_name + ".pdf"
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate
    counter = 1
    while True:
        candidate = f"{base_name}_{counter}.pdf"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        counter += 1

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'files' not in request.files:
        return jsonify({"error": "No se enviaron archivos"}), 400

    files = request.files.getlist('files')
    if not files or all(f.filename == '' for f in files):
        return jsonify({"error": "No se seleccionaron archivos"}), 400

    saved = []
    for f in files:
        if f and allowed_file(f.filename):
            filename = secure_filename(f.filename)
            path     = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            base, ext = os.path.splitext(filename)
            counter  = 1
            while os.path.exists(path):
                path = os.path.join(app.config['UPLOAD_FOLDER'], f"{base}_{counter}{ext}")
                counter += 1
            f.save(path)
            saved.append((path, f.filename))

    if not saved:
        return jsonify({"error": "Ningún archivo PDF válido fue subido"}), 400

    results    = []
    used_names = set()

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_file = {
            executor.submit(process_pdf, path, orig): (path, orig)
            for path, orig in saved
        }
        for future in as_completed(future_to_file):
            results.append(future.result())

    for res in results:
        final_name = get_unique_filename(
            res["detected_name"], app.config['PROCESSED_FOLDER'], used_names
        )
        res["final_filename"] = final_name

        orig_path = None
        for path, orig in saved:
            if orig == res["original"]:
                orig_path = path
                break

        if orig_path and os.path.exists(orig_path):
            import shutil
            shutil.copy2(orig_path, os.path.join(app.config['PROCESSED_FOLDER'], final_name))

    success_count = sum(1 for r in results if r["status"] == "success")

    return jsonify({
        "results": results,
        "summary": {
            "total":   len(results),
            "success": success_count,
            "errors":  len(results) - success_count
        }
    })

@app.route('/download/<path:filename>')
def download_file(filename):
    filepath = os.path.join(app.config['PROCESSED_FOLDER'], filename)
    if not os.path.exists(filepath):
        return f"Archivo no encontrado: {filename}", 404
    return send_file(filepath, as_attachment=True, download_name=os.path.basename(filepath))

@app.route('/download-zip')
def download_zip():
    processed_folder = app.config['PROCESSED_FOLDER']
    files = [f for f in os.listdir(processed_folder) if f.endswith('.pdf')]
    if not files:
        return "No hay archivos para descargar", 404

    zip_path = os.path.join(processed_folder, 'documentos_renombrados.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(os.path.join(processed_folder, f), f)

    return send_file(zip_path, as_attachment=True, download_name='documentos_renombrados.zip')

@app.route('/clear', methods=['POST'])
def clear_files():
    import shutil
    for folder in [app.config['UPLOAD_FOLDER'], app.config['PROCESSED_FOLDER']]:
        for f in os.listdir(folder):
            fp = os.path.join(folder, f)
            if os.path.isfile(fp):
                os.remove(fp)
    return jsonify({"message": "Archivos eliminados correctamente"})

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'],    exist_ok=True)
    os.makedirs(app.config['PROCESSED_FOLDER'], exist_ok=True)
    app.run(debug=True, port=5000, threaded=True)
