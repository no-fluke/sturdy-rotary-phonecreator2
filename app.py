import os
import re
import json
import base64
import uuid
import html          # for unescaping HTML entities
from io import BytesIO
from PIL import Image
from flask import Flask, render_template, request, jsonify, Response

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB
app.config['TEMP_QUIZ_DATA'] = {}  # temporary storage

# -------------------------------
# TXT PARSER (from Telegram bot – supports multiple formats)
# -------------------------------
def parse_txt_file(content):
    """Parse various TXT file formats and extract questions"""
    questions = []
    
    # Split by double newlines or question patterns
    blocks = re.split(r'\n\s*\n|(?=Q\.\d+|\d+\.\s*[A-Z])', content.strip())
    
    for block in blocks:
        block = block.strip()
        if not block:
            continue
            
        lines = [line.strip() for line in block.split('\n') if line.strip()]
        if len(lines) < 3:  # Minimum lines for a question
            continue
        
        question = {
            "question": "",
            "option_1": "", "option_2": "", "option_3": "", "option_4": "", "option_5": "",
            "answer": "",
            "solution_text": "",
            "question_image": "",
            "option_image_1": "", "option_image_2": "", "option_image_3": "",
            "option_image_4": "", "option_image_5": "",
            "solution_image": "",
            "correct_score": "3",
            "negative_score": "1",
            "section": ""  # will be filled later
        }
        
        current_line = 0
        
        # Detect format and parse accordingly
        if re.match(r'^(?:\d+\.\s*|Q\.\d+\s+)', lines[0]):
            # Format 1: "1. Question" or "Q.1 Question"
            question_text = re.sub(r'^(?:\d+\.\s*|Q\.\d+\s+)', '', lines[0])
            question_lines = [question_text]
            current_line = 1
            
            # Check if next line is Hindi question (not starting with option pattern)
            while (current_line < len(lines) and 
                   not re.match(r'^[a-e]\)\s*|^\([a-e]\)\s*|^[a-e]\.\s*', lines[current_line], re.IGNORECASE)):
                question_lines.append(lines[current_line])
                current_line += 1
        else:
            # Format without question number
            question_lines = []
            while (current_line < len(lines) and 
                   not re.match(r'^[a-e]\)\s*|^\([a-e]\)\s*|^[a-e]\.\s*', lines[current_line], re.IGNORECASE)):
                question_lines.append(lines[current_line])
                current_line += 1
        
        question["question"] = '<br>'.join(question_lines)
        
        # Extract options (up to 5)
        option_count = 0
        option_pattern = re.compile(r'^([a-e])[\)\.]\s*|^\(([a-e])\)\s*', re.IGNORECASE)
        
        while (current_line < len(lines) and option_count < 5 and
               (option_pattern.match(lines[current_line]) or 
                re.match(r'^Correct|^Answer:|^ex:', lines[current_line], re.IGNORECASE) is None)):
            
            if option_pattern.match(lines[current_line]):
                option_key = f"option_{option_count + 1}"
                option_text = lines[current_line]
                current_line += 1
                
                # Add next line if it's Hindi text (doesn't start with option pattern, Correct, or ex:)
                if (current_line < len(lines) and 
                    not re.match(r'^[a-e]\)|^\([a-e]\)|^[a-e]\.|^Correct|^Answer:|^ex:', 
                                lines[current_line], re.IGNORECASE)):
                    option_text += f"<br>{lines[current_line]}"
                    current_line += 1
                
                question[option_key] = option_text
                option_count += 1
            else:
                current_line += 1
        
        # Extract correct answer
        while current_line < len(lines):
            line = lines[current_line]
            # Check for various answer formats
            if re.match(r'^Correct\s*(?:option)?\s*[:-]', line, re.IGNORECASE):
                match = re.search(r'[:-]\s*([a-e])', line, re.IGNORECASE)
                if match:
                    ans = match.group(1).lower()
                    answer_map = {'a': '1', 'b': '2', 'c': '3', 'd': '4', 'e': '5'}
                    question["answer"] = answer_map.get(ans, '1')
            elif re.match(r'^Answer\s*[:-]', line, re.IGNORECASE):
                match = re.search(r'\(([a-e])\)', line, re.IGNORECASE)
                if not match:
                    match = re.search(r'[:-]\s*([a-e])', line, re.IGNORECASE)
                if match:
                    ans = match.group(1).lower()
                    answer_map = {'a': '1', 'b': '2', 'c': '3', 'd': '4', 'e': '5'}
                    question["answer"] = answer_map.get(ans, '1')
            current_line += 1
        
        # Extract explanation
        solution_lines = []
        for i in range(len(lines)):
            if re.match(r'^ex:', lines[i], re.IGNORECASE):
                solution_lines.append(re.sub(r'^ex:\s*', '', lines[i], flags=re.IGNORECASE))
        
        question["solution_text"] = '<br>'.join(solution_lines)
        
        # Only add if we have question and at least one option
        if question["question"] and (question["option_1"] or question["option_2"]):
            questions.append(question)
    
    return questions


# -------------------------------
# HTML PARSER (for the sample quiz HTML files)
# -------------------------------
def parse_html_file(content):
    """Parse HTML file containing embedded quiz data (like the samples)."""
    # Try to find const questions = [...];
    questions_match = re.search(r'const\s+questions\s*=\s*(\[.*?\]);', content, re.DOTALL)
    if questions_match:
        try:
            qlist = json.loads(questions_match.group(1))
            result = []
            for q in qlist:
                internal_q = {
                    "question": q.get("question", ""),
                    "option_1": q.get("option_1", ""),
                    "option_2": q.get("option_2", ""),
                    "option_3": q.get("option_3", ""),
                    "option_4": q.get("option_4", ""),
                    "option_5": q.get("option_5", ""),
                    "answer": str(q.get("answer", "")),
                    "solution_text": q.get("solution_text", ""),
                    "question_image": q.get("question_image", ""),
                    "option_image_1": q.get("option_image_1", ""),
                    "option_image_2": q.get("option_image_2", ""),
                    "option_image_3": q.get("option_image_3", ""),
                    "option_image_4": q.get("option_image_4", ""),
                    "option_image_5": q.get("option_image_5", ""),
                    "solution_image": q.get("solution_image", ""),
                    "correct_score": q.get("positive_marks", "2.00") or "2.00",
                    "negative_score": q.get("negative_marks", "0.50") or "0.50",
                    "section": ""
                }
                result.append(internal_q)
            return result
        except Exception as e:
            print("JSON parse error (questions):", e)
            # fall through to other pattern

    # Try const quizData = {...};
    quizdata_match = re.search(r'const\s+quizData\s*=\s*(\{.*?\});', content, re.DOTALL)
    if quizdata_match:
        try:
            quizdata = json.loads(quizdata_match.group(1))
            qlist = quizdata.get("questions", [])
            result = []
            for q in qlist:
                options = q.get("options", [])
                internal_q = {
                    "question": q.get("text", ""),
                    "option_1": options[0] if len(options) > 0 else "",
                    "option_2": options[1] if len(options) > 1 else "",
                    "option_3": options[2] if len(options) > 2 else "",
                    "option_4": options[3] if len(options) > 3 else "",
                    "option_5": options[4] if len(options) > 4 else "",
                    "answer": str(q.get("correctIndex", 0) + 1),  # convert to 1-indexed string
                    "solution_text": q.get("explanation", ""),
                    "question_image": "",
                    "option_image_1": "",
                    "option_image_2": "",
                    "option_image_3": "",
                    "option_image_4": "",
                    "option_image_5": "",
                    "solution_image": "",
                    "correct_score": "1",
                    "negative_score": "0.25",
                    "section": ""
                }
                result.append(internal_q)
            return result
        except Exception as e:
            print("JSON parse error (quizData):", e)

    # If neither pattern found, return empty list
    return []


# -------------------------------
# TXT FORMATTER (for download in CHSL polity mock style)
# -------------------------------
def strip_html(text):
    """Remove HTML tags, unescape entities, and collapse whitespace."""
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def questions_to_txt(questions):
    """Convert the internal question list to the CHSL polity mock TXT format."""
    lines = []
    for idx, q in enumerate(questions, start=1):
        # Question number and text
        q_text = strip_html(q.get('question', ''))
        lines.append(f"{idx}. {q_text}")

        # Options a) to e)
        for opt_num in range(1, 6):
            opt_key = f'option_{opt_num}'
            if opt_key in q and q[opt_key]:
                opt_text = strip_html(q[opt_key])
                opt_letter = chr(96 + opt_num)  # a=1, b=2, ...
                lines.append(f"{opt_letter}) {opt_text}")

        # Correct answer
        ans = q.get('answer', '')
        if ans:
            try:
                ans_int = int(ans)
                ans_letter = chr(96 + ans_int)
            except ValueError:
                ans_letter = ans  # fallback, e.g. if answer already a letter
            lines.append(f"Correct option:-{ans_letter}")

        # Explanation
        expl = strip_html(q.get('solution_text', ''))
        if expl:
            lines.append(f"ex: {expl}")
        else:
            lines.append("ex: No explanation provided.")

        # Blank line between questions
        lines.append("")

    return "\n".join(lines)


# -------------------------------
# IMAGE PROCESSOR
# -------------------------------
def process_image(file_storage, max_size=(700, 700), quality=60):
    try:
        image = Image.open(file_storage)

        if image.mode in ('RGBA', 'LA'):
            bg = Image.new('RGB', image.size, (255, 255, 255))
            bg.paste(image, mask=image.split()[-1])
            image = bg
        elif image.mode == 'P':
            image = image.convert("RGB")

        image.thumbnail(max_size, Image.Resampling.LANCZOS)

        buffer = BytesIO()
        image.save(buffer, format='JPEG', quality=quality, optimize=True)
        buffer.seek(0)

        base64_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return f"data:image/jpeg;base64,{base64_str}"

    except Exception as e:
        print("Image error:", e)
        return None


# -------------------------------
# ROUTES
# -------------------------------
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    quiz_type = request.form.get('quiz_type', 'topic')

    try:
        if quiz_type == 'topic':
            file = request.files.get('file')
            if not file:
                return jsonify({'error': 'No file'}), 400

            filename = file.filename.lower()
            content = file.read().decode('utf-8', errors='ignore')

            if filename.endswith('.txt'):
                questions = parse_txt_file(content)
            elif filename.endswith(('.html', '.htm')):
                questions = parse_html_file(content)
            else:
                return jsonify({'error': 'Unsupported file type. Please upload .txt or .html'}), 400

            if not questions:
                return jsonify({'error': 'No questions parsed'}), 400

            quiz_id = str(uuid.uuid4())
            app.config['TEMP_QUIZ_DATA'][quiz_id] = {
                "questions": questions,
                "quiz_type": quiz_type
            }
            return jsonify({'quiz_id': quiz_id})

        # FULL MOCK
        else:
            files = []
            sections = []
            for key in request.files:
                if key.startswith("file_"):
                    idx = key.split("_")[1]
                    file = request.files[key]
                    section_name = request.form.get(f'section_{idx}', '').strip()
                    if not file or not section_name:
                        return jsonify({'error': 'Section missing'}), 400

                    filename = file.filename.lower()
                    content = file.read().decode('utf-8', errors='ignore')

                    if filename.endswith('.txt'):
                        qs = parse_txt_file(content)
                    elif filename.endswith(('.html', '.htm')):
                        qs = parse_html_file(content)
                    else:
                        return jsonify({'error': f'Unsupported file type for section {section_name}. Use .txt or .html'}), 400

                    for q in qs:
                        q["section"] = section_name

                    files.extend(qs)
                    sections.append(section_name)

            if not files:
                return jsonify({'error': 'No questions parsed'}), 400

            quiz_id = str(uuid.uuid4())
            app.config['TEMP_QUIZ_DATA'][quiz_id] = {
                "questions": files,
                "quiz_type": quiz_type,
                "sections": sections
            }
            return jsonify({'quiz_id': quiz_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/preview/<quiz_id>')
def preview(quiz_id):
    data = app.config['TEMP_QUIZ_DATA'].get(quiz_id)
    if not data:
        return "Quiz not found", 404

    return render_template(
        'preview.html',
        quiz_id=quiz_id,
        questions=data["questions"],          # direct list – tojson in template
        quiz_type=data["quiz_type"],
        sections=data.get("sections", [])
    )


@app.route('/download_txt/<quiz_id>')
def download_txt(quiz_id):
    """Download the parsed questions as a plain text file in CHSL polity mock format."""
    data = app.config['TEMP_QUIZ_DATA'].get(quiz_id)
    if not data:
        return "Quiz not found", 404

    questions = data["questions"]
    txt_content = questions_to_txt(questions)

    response = Response(txt_content, mimetype='text/plain')
    response.headers.set('Content-Disposition', 'attachment', filename=f"quiz_{quiz_id}.txt")
    return response


@app.route('/upload_image', methods=['POST'])
def upload_image():
    file = request.files.get('image')
    if not file or not file.mimetype.startswith("image/"):
        return jsonify({'error': 'Invalid image'}), 400

    base64_img = process_image(file)
    return jsonify({'base64': base64_img}) if base64_img else jsonify({'error': 'Failed'}), 500


@app.route('/generate', methods=['POST'])
def generate():
    data = request.get_json() or {}
    questions = data.get("questions", [])
    quiz_name = data.get("quiz_name", "Quiz")
    quiz_type = data.get("quiz_type", "topic")
    time_minutes = int(data.get("time", 25))

    template_file = 'templates/quiz_template_full.html' if quiz_type == 'full' else 'templates/quiz_template_topic.html'

    with open(template_file, 'r', encoding='utf-8') as f:
        template = f.read()

    html = template.replace("{quiz_name}", quiz_name)
    html = html.replace("{questions_array}", json.dumps(questions, ensure_ascii=False))
    html = html.replace("{seconds}", str(time_minutes * 60))

    response = Response(html, mimetype='text/html')
    response.headers.set('Content-Disposition', 'attachment', filename=f"{quiz_name}.html")
    return response


if __name__ == '__main__':
    app.run(debug=True)
