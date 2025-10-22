from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
import pytz
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'fallback-secret-key')

# South Africa timezone
sa_timezone = pytz.timezone('Africa/Johannesburg')

# Database configuration
def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    
    try:
        conn = psycopg2.connect(database_url)
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def init_db():
    """Initialize database tables"""
    conn = get_db_connection()
    if conn is None:
        print("Failed to connect to database")
        return
    
    cur = conn.cursor()
    
    try:
        # Create students table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS students (
                id SERIAL PRIMARY KEY,
                student_number VARCHAR(20) UNIQUE NOT NULL,
                name_surname VARCHAR(100) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create complaints table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS complaints (
                id SERIAL PRIMARY KEY,
                complaint_number INTEGER NOT NULL,
                name_surname VARCHAR(100) NOT NULL,
                student_number VARCHAR(20) NOT NULL,
                student_email VARCHAR(100) NOT NULL,
                block_number VARCHAR(10) NOT NULL,
                unit_number VARCHAR(10) NOT NULL,
                room_number VARCHAR(10) NOT NULL,
                complaint_text TEXT NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP WITH TIME ZONE
            )
        ''')
        
        # Create admin table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS admin (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Check if admin exists, if not create default admin
        cur.execute("SELECT COUNT(*) FROM admin WHERE username = %s", (os.getenv('ADMIN_USERNAME'),))
        if cur.fetchone()[0] == 0:
            password_hash = generate_password_hash(os.getenv('ADMIN_PASSWORD'))
            cur.execute(
                "INSERT INTO admin (username, password_hash) VALUES (%s, %s)",
                (os.getenv('ADMIN_USERNAME'), password_hash)
            )
        
        conn.commit()
        print("Database initialized successfully")
        
    except Exception as e:
        print(f"Database initialization error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

# Initialize database when app starts
with app.app_context():
    init_db()

# Routes
@app.route('/')
def index():
    return redirect(url_for('complaint_form'))

@app.route('/complaint-form')
def complaint_form():
    return render_template('form.html')

@app.route('/submit-complaint', methods=['POST'])
def submit_complaint():
    try:
        # Get form data
        name_surname = request.form.get('name_surname')
        student_number = request.form.get('student_number')
        student_email = request.form.get('student_email')
        block_number = request.form.get('block_number')
        unit_number = request.form.get('unit_number')
        room_number = request.form.get('room_number')
        complaint_text = request.form.get('complaint_text')

        conn = get_db_connection()
        if conn is None:
            return jsonify({
                'success': False,
                'message': 'Database connection error. Please try again.'
            }), 500

        cur = conn.cursor()

        # Check if student exists
        cur.execute("SELECT * FROM students WHERE student_number = %s", (student_number,))
        student = cur.fetchone()
        
        if not student:
            cur.close()
            conn.close()
            return jsonify({
                'success': False,
                'message': 'Student number not found in our system. Please contact administration.'
            }), 400

        # Get today's complaint count for numbering
        today = datetime.now(sa_timezone).date()
        cur.execute(
            "SELECT COUNT(*) FROM complaints WHERE DATE(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Johannesburg') = %s",
            (today,)
        )
        today_complaints_count = cur.fetchone()[0]
        complaint_number = today_complaints_count + 1

        # Create new complaint
        cur.execute('''
            INSERT INTO complaints 
            (complaint_number, name_surname, student_number, student_email, block_number, unit_number, room_number, complaint_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ''', (complaint_number, name_surname, student_number, student_email, block_number, unit_number, room_number, complaint_text))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Complaint submitted successfully! Your complaint number is: {complaint_number}',
            'complaint_number': complaint_number
        })

    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
            if 'cur' in locals():
                cur.close()
            conn.close()
        return jsonify({
            'success': False,
            'message': 'An error occurred while submitting your complaint. Please try again.'
        }), 500

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db_connection()
        if conn is None:
            flash('Database connection error', 'error')
            return render_template('admin_login.html')
        
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM admin WHERE username = %s", (username,))
        admin_user = cur.fetchone()
        cur.close()
        conn.close()
        
        if admin_user and check_password_hash(admin_user[2], password):
            session['admin_logged_in'] = True
            session['admin_username'] = username
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid credentials', 'error')
    
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error', 'error')
        return render_template('admin_dashboard.html', complaints=[])
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Get search parameters
    search_date = request.args.get('search_date')
    
    try:
        if search_date:
            try:
                search_date_obj = datetime.strptime(search_date, '%Y-%m-%d').date()
                cur.execute(
                    "SELECT * FROM complaints WHERE DATE(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Johannesburg') = %s ORDER BY created_at DESC",
                    (search_date_obj,)
                )
            except ValueError:
                cur.execute("SELECT * FROM complaints ORDER BY created_at DESC")
        else:
            cur.execute("SELECT * FROM complaints ORDER BY created_at DESC")
        
        complaints = cur.fetchall()
    except Exception as e:
        print(f"Error fetching complaints: {e}")
        complaints = []
    finally:
        cur.close()
        conn.close()
    
    return render_template('admin_dashboard.html', complaints=complaints)

@app.route('/admin/students')
def admin_students():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    if conn is None:
        flash('Database connection error', 'error')
        return render_template('admin_students.html', students=[])
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("SELECT * FROM students ORDER BY created_at DESC")
        students = cur.fetchall()
    except Exception as e:
        print(f"Error fetching students: {e}")
        students = []
    finally:
        cur.close()
        conn.close()
    
    return render_template('admin_students.html', students=students)

@app.route('/admin/update-status/<int:complaint_id>', methods=['POST'])
def update_status(complaint_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    new_status = request.json.get('status')
    
    if new_status not in ['pending', 'completed']:
        return jsonify({'success': False, 'message': 'Invalid status'})
    
    conn = get_db_connection()
    if conn is None:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    cur = conn.cursor()
    
    try:
        if new_status == 'completed':
            cur.execute(
                "UPDATE complaints SET status = %s, completed_at = %s WHERE id = %s",
                (new_status, datetime.now(sa_timezone), complaint_id)
            )
        else:
            cur.execute(
                "UPDATE complaints SET status = %s, completed_at = NULL WHERE id = %s",
                (new_status, complaint_id)
            )
        
        conn.commit()
        return jsonify({'success': True})
    
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': 'Error updating status'}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/admin/add-student', methods=['POST'])
def add_student():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    student_number = request.json.get('student_number')
    name_surname = request.json.get('name_surname')
    
    if not student_number or not name_surname:
        return jsonify({'success': False, 'message': 'Student number and name are required'}), 400
    
    conn = get_db_connection()
    if conn is None:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    cur = conn.cursor()
    
    try:
        # Check if student already exists
        cur.execute("SELECT * FROM students WHERE student_number = %s", (student_number,))
        existing_student = cur.fetchone()
        
        if existing_student:
            return jsonify({'success': False, 'message': 'Student number already exists'}), 400
        
        # Add new student with name_surname
        cur.execute("INSERT INTO students (student_number, name_surname) VALUES (%s, %s)", (student_number, name_surname))
        conn.commit()
        return jsonify({'success': True, 'message': 'Student added successfully'})
    
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': 'Error adding student'}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/admin/delete-student/<int:student_id>', methods=['POST'])
def delete_student(student_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    conn = get_db_connection()
    if conn is None:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    cur = conn.cursor()
    
    try:
        # Check if student has any complaints
        cur.execute("SELECT COUNT(*) FROM complaints WHERE student_number = (SELECT student_number FROM students WHERE id = %s)", (student_id,))
        complaint_count = cur.fetchone()[0]
        
        if complaint_count > 0:
            return jsonify({'success': False, 'message': 'Cannot delete student with existing complaints'}), 400
        
        # Delete student
        cur.execute("DELETE FROM students WHERE id = %s", (student_id,))
        conn.commit()
        return jsonify({'success': True, 'message': 'Student deleted successfully'})
    
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': 'Error deleting student'}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    return redirect(url_for('admin_login'))

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)