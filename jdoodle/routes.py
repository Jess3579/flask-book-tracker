import os
import json
import sqlite3
import requests
from io import BytesIO
from datetime import datetime, timedelta
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, abort, send_file
)

# groups routes together
main_blueprint = Blueprint('main', __name__)

# database file
DB_PATH = 'books.db'

# config file path
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')


def load_config():
    # loads config file
    """Load configuration from config.json."""
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        # default if missing
        return {"CACHE_DURATION_MINUTES": 5}
    except json.JSONDecodeError:
        # default if broken
        return {"CACHE_DURATION_MINUTES": 5}


# saves config
CONFIG = load_config()


def get_db_connection():
    # connects to database
    """Open SQLite connection and return rows by column name."""
    conn = sqlite3.connect(DB_PATH)
    # lets me use column names
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    # creates tables if needed
    """Create database tables if they do not exist."""
    conn = get_db_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT,
                openlibrary_key TEXT,
                first_publish_year INTEGER,
                cover_id INTEGER,
                isbn TEXT,
                cached_data TEXT,
                cached_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS reading_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (book_id) REFERENCES books(id)
            );
        """)
        conn.commit()
    finally:
        conn.close()


def is_cache_fresh(cached_at_str):
    # checks if cache is still good
    """Check if cached data is still fresh based on config duration."""
    if not cached_at_str:
        return False

    try:
        cached_at = datetime.fromisoformat(cached_at_str)
        duration = timedelta(minutes=CONFIG.get("CACHE_DURATION_MINUTES", 5))
        return datetime.now() - cached_at < duration
    except Exception:
        return False


def fetch_books_from_api(query):
    # calls api
    """
    Fetch books from Open Library Search API.
    Returns a list of simplified book dictionaries.
    """
    url = "https://openlibrary.org/search.json"
    params = {"q": query}

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()

    data = response.json()
    docs = data.get("docs", [])[:10]

    books = []
    for item in docs:
        # builds simple book data
        books.append({
            "title": item.get("title", "No title"),
            "author": ", ".join(item.get("author_name", [])) if item.get("author_name") else "Unknown",
            "openlibrary_key": item.get("key", ""),
            "first_publish_year": item.get("first_publish_year"),
            "cover_id": item.get("cover_i"),
            "isbn": item.get("isbn", [None])[0] if item.get("isbn") else None,
            "raw_json": item
        })

    return books


@main_blueprint.route('/')
def index():
    # home page
    conn = get_db_connection()
    try:
        # count books
        total_books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        # count completed
        total_completed = conn.execute("""
            SELECT COUNT(*) 
            FROM reading_logs
            WHERE status = 'Completed'
        """).fetchone()[0]
        # count reading
        total_reading = conn.execute("""
            SELECT COUNT(*)
            FROM reading_logs
            WHERE status = 'Reading'
        """).fetchone()[0]

        # recent books
        recent_books = conn.execute("""
            SELECT books.*, reading_logs.status
            FROM books
            LEFT JOIN reading_logs ON books.id = reading_logs.book_id
            ORDER BY books.created_at DESC
            LIMIT 5
        """).fetchall()

        return render_template(
            'index.html',
            total_books=total_books,
            total_completed=total_completed,
            total_reading=total_reading,
            recent_books=recent_books
        )
    finally:
        conn.close()


@main_blueprint.route('/books')
def books():
    # shows books
    status_filter = request.args.get('status', '').strip()

    conn = get_db_connection()
    try:
        if status_filter:
            # filtered
            books = conn.execute("""
                SELECT books.*, reading_logs.status, reading_logs.notes
                FROM books
                LEFT JOIN reading_logs ON books.id = reading_logs.book_id
                WHERE reading_logs.status = ?
                ORDER BY books.created_at DESC
            """, (status_filter,)).fetchall()
        else:
            # all books
            books = conn.execute("""
                SELECT books.*, reading_logs.status, reading_logs.notes
                FROM books
                LEFT JOIN reading_logs ON books.id = reading_logs.book_id
                ORDER BY books.created_at DESC
            """).fetchall()

        return render_template('books.html', books=books, status_filter=status_filter)
    finally:
        conn.close()


@main_blueprint.route('/books/<int:book_id>')
def book_detail(book_id):
    # single book page
    conn = get_db_connection()
    try:
        book = conn.execute("""
            SELECT books.*, reading_logs.status, reading_logs.notes, reading_logs.updated_at
            FROM books
            LEFT JOIN reading_logs ON books.id = reading_logs.book_id
            WHERE books.id = ?
        """, (book_id,)).fetchone()
    finally:
        conn.close()

    if book is None:
        abort(404)

    return render_template('detail.html', book=book)


@main_blueprint.route('/search', methods=['GET', 'POST'])
def search():
    # search page
    results = []
    query = ''

    if request.method == 'POST':
        # get search input
        query = request.form.get('query', '').strip()

        if not query:
            flash('Please enter a search term.', 'danger')
            return render_template('search.html', results=results, query=query)

        try:
            # call api
            results = fetch_books_from_api(query)
            if not results:
                flash('No books were found.', 'warning')
        except requests.RequestException:
            flash('Could not reach the book API. Please try again.', 'danger')
        except Exception:
            flash('Something went wrong while searching.', 'danger')

    return render_template('search.html', results=results, query=query)


@main_blueprint.route('/add', methods=['GET', 'POST'])
@main_blueprint.route('/books/add', methods=['GET', 'POST'])
def add_book():
    # add book page
    if request.method == 'POST':
        # get form data
        title = request.form.get('title', '').strip()
        author = request.form.get('author', '').strip()
        openlibrary_key = request.form.get('openlibrary_key', '').strip()
        first_publish_year = request.form.get('first_publish_year', '').strip()
        cover_id = request.form.get('cover_id', '').strip()
        isbn = request.form.get('isbn', '').strip()
        status = request.form.get('status', '').strip()
        notes = request.form.get('notes', '').strip()

        # check title
        if not title:
            flash('Title is required.', 'danger')
            return render_template('add.html')

        # check status
        if not status:
            flash('Reading status is required.', 'danger')
            return render_template('add.html')

        conn = get_db_connection()
        try:
            # insert book
            cursor = conn.execute("""
                INSERT INTO books (title, author, openlibrary_key, first_publish_year, cover_id, isbn, cached_data, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                title,
                author,
                openlibrary_key or None,
                int(first_publish_year) if first_publish_year else None,
                int(cover_id) if cover_id else None,
                isbn or None,
                None,
                None
            ))

            # get id
            book_id = cursor.lastrowid

            # insert log
            conn.execute("""
                INSERT INTO reading_logs (book_id, status, notes)
                VALUES (?, ?, ?)
            """, (book_id, status, notes))

            conn.commit()
            flash('Book added successfully.', 'success')
            return redirect(url_for('main.book_detail', book_id=book_id))
        except sqlite3.Error:
            flash('Database error while adding book.', 'danger')
            return render_template('add.html')
        finally:
            conn.close()

    return render_template('add.html')


@main_blueprint.route('/edit/<int:book_id>', methods=['GET', 'POST'])
@main_blueprint.route('/books/<int:book_id>/edit', methods=['GET', 'POST'])
def edit_book(book_id):
    # edit book page
    conn = get_db_connection()
    try:
        # get current book data
        book = conn.execute("""
            SELECT books.*, reading_logs.status, reading_logs.notes
            FROM books
            LEFT JOIN reading_logs ON books.id = reading_logs.book_id
            WHERE books.id = ?
        """, (book_id,)).fetchone()

        if book is None:
            abort(404)

        if request.method == 'POST':
            # get updated form data
            title = request.form.get('title', '').strip()
            author = request.form.get('author', '').strip()
            status = request.form.get('status', '').strip()
            notes = request.form.get('notes', '').strip()

            # check title
            if not title:
                flash('Title is required.', 'danger')
                return render_template('edit.html', book=book)

            # check status
            if not status:
                flash('Reading status is required.', 'danger')
                return render_template('edit.html', book=book)

            # update book
            conn.execute("""
                UPDATE books
                SET title = ?, author = ?
                WHERE id = ?
            """, (title, author, book_id))

            # update log
            conn.execute("""
                UPDATE reading_logs
                SET status = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE book_id = ?
            """, (status, notes, book_id))

            conn.commit()
            flash('Book updated successfully.', 'success')
            return redirect(url_for('main.book_detail', book_id=book_id))

        return render_template('edit.html', book=book)
    finally:
        conn.close()


@main_blueprint.route('/delete/<int:book_id>', methods=['POST'])
@main_blueprint.route('/books/<int:book_id>/delete', methods=['POST'])
def delete_book(book_id):
    # delete book
    conn = get_db_connection()
    try:
        # check if book exists
        book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()

        if book is None:
            abort(404)

        # delete log first
        conn.execute("DELETE FROM reading_logs WHERE book_id = ?", (book_id,))
        # delete book
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        conn.commit()

        flash('Book deleted successfully.', 'success')
        return redirect(url_for('main.books'))
    finally:
        conn.close()


@main_blueprint.route('/books/<int:book_id>/refresh', methods=['POST'])
def refresh_book(book_id):
    # refresh book data
    conn = get_db_connection()
    try:
        # get saved book
        book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()

        if book is None:
            abort(404)

        # use cache if still good
        if is_cache_fresh(book['cached_at']):
            flash('Data is already up to date.', 'info')
            return redirect(url_for('main.book_detail', book_id=book_id))

        # search api again
        query = book['title']
        results = fetch_books_from_api(query)

        if results:
            first_result = results[0]
            # update saved data
            conn.execute("""
                UPDATE books
                SET cached_data = ?, cached_at = ?, author = ?, first_publish_year = ?, cover_id = ?, isbn = ?
                WHERE id = ?
            """, (
                json.dumps(first_result['raw_json']),
                datetime.now().isoformat(),
                first_result['author'],
                first_result['first_publish_year'],
                first_result['cover_id'],
                first_result['isbn'],
                book_id
            ))
            conn.commit()
            flash('Book data refreshed from API.', 'success')
        else:
            flash('No updated data found.', 'warning')

    except requests.RequestException:
        flash('Could not reach the API. Showing saved data.', 'warning')
    except Exception:
        flash('Something went wrong while refreshing.', 'danger')
    finally:
        conn.close()

    return redirect(url_for('main.book_detail', book_id=book_id))


@main_blueprint.route('/export/json')
def export_json():
    # export books as json
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT books.*, reading_logs.status, reading_logs.notes
            FROM books
            LEFT JOIN reading_logs ON books.id = reading_logs.book_id
            ORDER BY books.created_at DESC
        """).fetchall()

        books = []
        for row in rows:
            # build export data
            books.append({
                "title": row["title"],
                "author": row["author"],
                "status": row["status"],
                "notes": row["notes"],
                "first_publish_year": row["first_publish_year"],
                "openlibrary_key": row["openlibrary_key"],
                "created_at": row["created_at"]
            })

        return jsonify({"books": books}), 200

    finally:
        conn.close()


@main_blueprint.route('/api/books')
def api_books():
    # api route for all books
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT books.*, reading_logs.status, reading_logs.notes
            FROM books
            LEFT JOIN reading_logs ON books.id = reading_logs.book_id
            ORDER BY books.created_at DESC
        """).fetchall()

        return jsonify({'books': [dict(row) for row in rows]}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@main_blueprint.route('/api/books/<int:book_id>')
def api_book_detail(book_id):
    # api route for one book
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT books.*, reading_logs.status, reading_logs.notes
            FROM books
            LEFT JOIN reading_logs ON books.id = reading_logs.book_id
            WHERE books.id = ?
        """, (book_id,)).fetchone()
    finally:
        conn.close()

    if row is None:
        return jsonify({'error': 'Not found'}), 404

    return jsonify(dict(row)), 200


@main_blueprint.route('/api/stats')
def api_stats():
    # api route for stats
    conn = get_db_connection()
    try:
        # count books
        total_books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        # count completed
        total_completed = conn.execute("""
            SELECT COUNT(*) FROM reading_logs WHERE status = 'Completed'
        """).fetchone()[0]
        # count reading
        total_reading = conn.execute("""
            SELECT COUNT(*) FROM reading_logs WHERE status = 'Reading'
        """).fetchone()[0]

        return jsonify({
            'total_books': total_books,
            'total_completed': total_completed,
            'total_reading': total_reading
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@main_blueprint.app_errorhandler(404)
def not_found(error):
    # custom 404 page
    return render_template('404.html'), 404