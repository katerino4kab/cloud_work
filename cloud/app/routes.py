import re, datetime, shutil, os, uuid
from os import listdir, path, makedirs
from os.path import isfile, join
from PIL import Image
from flask import render_template, flash, redirect, url_for, request, abort, send_from_directory, send_file, jsonify
from flask_login import current_user, login_user, login_required, logout_user
import sqlalchemy as sa  # SQLAlchemy для работы с базой данных
from urllib.parse import urlsplit, unquote  # Для анализа URL
from werkzeug.utils import secure_filename

# Импорт компонентов приложения
from app import app, db, login  # Экземпляры приложения, БД и менеджера авторизации
from app.forms import LoginForm, RegisterForm, UploadFileForm  # Формы WTForms
from app.models import CloudFile, Folder, User  # Модель пользователя
from io import BytesIO

app.config[('UPLOAD_FOLDER')] = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static/uploads')


@app.route('/uploads/<path:filename>')
@login_required
def serve_file(filename):
    # Проверка безопасности пути
    # if not filename.startswith(str(current_user.id)):
    #     abort(403)

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    # Для изображений - возвращаем уменьшенную версию
    if file_path.split('.')[-1].lower() in ['png', 'jpg', 'jpeg', 'gif']:
        try:
            size = request.args.get('size', '300x300')
            width, height = map(int, size.split('x'))

            img = Image.open(file_path)
            img.thumbnail((width, height))

            img_io = BytesIO()
            img.save(img_io, 'JPEG' if file_path.lower().endswith('jpg') else 'PNG', quality=85)
            img_io.seek(0)

            return send_file(img_io, mimetype=f'image/{img.format.lower()}')

        except Exception as e:
            app.logger.error(f"Error processing image: {str(e)}")

    # Для обычных файлов
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/')
@app.route('/index')
@login_required  # Требует авторизации
def index():
    """Основная страница приложения"""
    return render_template('index.html')


@app.route('/login', methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index", username=current_user.login))

    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(sa.select(User).where(User.login == form.username.data))
        if user is None or not user.check_password(form.password.data):
            flash("invalid username or password")
            return redirect(url_for("login"))
        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get("next")
        if not next_page or urlsplit(next_page).netloc != "":
            next_page = url_for("index", username=user.login)
        return redirect(next_page)
    return render_template("login.html", title="Persik", form=form)


@app.route('/register', methods=["GET", "POST"])
def register_endpoint():
    form = RegisterForm()
    if form.validate_on_submit():
        user = User(login=form.username.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash("Поздравляем с регистрацией!")
        return redirect(url_for("login"))
    return render_template("register.html", form=form)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/bootstrap')
def show_bootstrap():
    return render_template('bootstrap.html')


# Профиль пользователя
@app.route('/profile')
@login_required
def profile():
    """Страница профиля пользователя"""
    return render_template('index.html', user=current_user)


# Обработка ошибки 404
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


# Исправим создание папок
@app.route('/create_folder', methods=['POST'])
@login_required
def create_folder():
    folder_name = request.form['folder_name']
    current_path = request.form.get('current_path', '')

    base_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(current_user.id))
    target_dir = os.path.join(base_dir, current_path)

    # Проверяем допустимость имени
    if not re.match(r'^[\w\-]+$', folder_name):
        flash('Недопустимое имя папки. Можно использовать буквы, цифры, подчеркивание и дефис', 'danger')
        return redirect(request.referrer)

    new_folder = os.path.join(target_dir, folder_name)

    try:
        os.makedirs(new_folder, exist_ok=False)
        flash('Папка успешно создана', 'success')
    except FileExistsError:
        flash('Папка с таким именем уже существует', 'warning')
    except Exception as e:
        app.logger.error(f"Folder creation error: {str(e)}")
        flash('Ошибка при создании папки', 'danger')

    return redirect(request.referrer)


@app.route('/delete', methods=['POST'])
@login_required
def delete_item():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid request'}), 400

        item_type = data.get('type')
        path_components = data.get('path').split('/')  

        if not item_type or not path_components:
            return jsonify({'error': 'Missing parameters'}), 400

        full_path = os.path.join(
            app.config['UPLOAD_FOLDER'],  
            str(current_user.id),        
            *path_components              
        )
        full_path = os.path.normpath(full_path)  # Нормализуем путь

        # Базовый путь для проверки безопасности
        base_dir = os.path.abspath(os.path.join(app.config['UPLOAD_FOLDER'], str(current_user.id)))

        # Проверка безопасности
        if not os.path.abspath(full_path).startswith(base_dir):
            app.logger.error(f"Security violation: {full_path} not in {base_dir}")
            return jsonify({'error': 'Access denied'}), 403

        # Проверка существования
        if not os.path.exists(full_path):
            app.logger.error(f"Path not found: {full_path}")
            return jsonify({'error': 'Path not found'}), 404

        # Удаление
        try:
            if item_type == 'folder':
                shutil.rmtree(full_path)
            else:
                os.remove(full_path)
            
            app.logger.info(f"Successfully deleted {item_type}: {full_path}")
            return jsonify({'status': 'success'}), 200

        except Exception as e:
            app.logger.error(f"Delete failed: {str(e)}")
            return jsonify({'error': str(e)}), 500

    except Exception as e:
        app.logger.error(f"Server error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/files', methods=['GET', 'POST'])
@app.route('/files/<path:folder_path>', methods=['GET', 'POST'])
@login_required
def files(folder_path=None):
    form = UploadFileForm()
    base_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(current_user.id))
    current_dir = os.path.join(base_dir, folder_path) if folder_path else base_dir

    # Создаем базовую структуру папок пользователя если не существует
    os.makedirs(base_dir, exist_ok=True)

    # Обработка загрузки файла
    if form.validate_on_submit():
        file = form.file.data
        target_folder = request.form.get('target_folder', '')  # Пустая строка по умолчанию

        if file:
            filename = secure_filename(file.filename)

            # Определяем путь для сохранения
            save_dir = os.path.join(current_dir, target_folder) if target_folder else current_dir

            # Создаем целевую директорию если не существует
            os.makedirs(save_dir, exist_ok=True)

            # Обработка дубликатов
            dest_path = os.path.join(save_dir, filename)
            if os.path.exists(dest_path):
                filename = f"{uuid.uuid4().hex}_{filename}"
                dest_path = os.path.join(save_dir, filename)

            file.save(dest_path)
            flash('Файл успешно загружен', 'success')
            return redirect(request.url)
        else:
            flash('Недопустимый тип файла', 'danger')

    # Получение списка файлов и папок
    try:
        items = os.listdir(current_dir)
    except FileNotFoundError:
        os.makedirs(current_dir, exist_ok=True)
        items = []

    # Формируем данные для отображения
    subfolders = []
    files = []

    for item in items:
        item_path = os.path.join(current_dir, item)
        rel_path = os.path.join(folder_path, item) if folder_path else item

        if os.path.isdir(item_path):
            try:
                stat = os.stat(item_path)
                subfolders.append({
                    'name': item,
                    'full_path': rel_path.replace('\\', '/'),
                    'size': get_folder_size(item_path),
                    'modified': stat.st_mtime,
                    'item_count': len(os.listdir(item_path))
                })
            except Exception as e:
                app.logger.error(f"Error processing folder {item}: {str(e)}")
        else:
            try:
                stat = os.stat(item_path)
                files.append({
                    'name': item,
                    'type': item.split('.')[-1].lower() if '.' in item else 'file',
                    'size': stat.st_size,
                    'modified': stat.st_mtime,
                    'full_path': os.path.join(str(current_user.id), rel_path).replace('\\', '/')
                })
            except Exception as e:
                app.logger.error(f"Error processing file {item}: {str(e)}")

    return render_template('files.html',
                           subfolders=subfolders,
                           files=files,
                           current_path=folder_path or '',
                           form=form)


def get_folder_size(start_path):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(start_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total_size += os.path.getsize(fp)
    return total_size


@app.route('/download/<path:filename>')
@login_required
def download_file(filename):
    try:
        # Нормализация пути
        filename = filename.replace('\\', '/')

        # Формируем полный путь к файлу
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

        # Проверка существования файла
        if not os.path.isfile(file_path):
            app.logger.error(f"File not found: {file_path}")
            abort(404)

        # Проверка безопасности - файл должен находиться в папке пользователя
        if not file_path.startswith(os.path.join(app.config['UPLOAD_FOLDER'], str(current_user.id))):
            app.logger.error(f"Security violation: {file_path}")
            abort(403)

        # Определяем имя файла для скачивания (последняя часть пути)
        download_name = os.path.basename(filename)

        # Отправляем файл с правильным MIME-типом
        return send_file(
            file_path,
            as_attachment=True,
            download_name=download_name,
            mimetype=None  # Flask автоматически определит MIME-тип
        )

    except Exception as e:
        app.logger.error(f"Download error: {str(e)}")
        abort(500)


# Регистрация фильтра даты
@app.template_filter('datetimeformat')
def datetimeformat_filter(value, format='%d.%m.%Y %H:%M'):
    if isinstance(value, datetime.datetime):
        return value.strftime(format)
    try:
        return datetime.datetime.fromtimestamp(value).strftime(format)
    except TypeError:
        return str(value)
