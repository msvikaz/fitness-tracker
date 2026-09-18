from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
from functools import wraps
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-fallback-key-change-in-production')

# ---- Database setup ----
def init_db():
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            exercise TEXT NOT NULL,
            sets INTEGER NOT NULL,
            reps INTEGER NOT NULL,
            weight REAL NOT NULL,
            distance REAL DEFAULT 0,
            date TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

# ---- Helper: calculate dashboard stats for a user ----
def get_stats(user_id):
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()

    # Total workouts logged
    cursor.execute('SELECT COUNT(*) FROM workouts WHERE user_id = ?', (user_id,))
    total_workouts = cursor.fetchone()[0]

    # Total volume lifted (sets x reps x weight, summed)
    cursor.execute('SELECT sets, reps, weight FROM workouts WHERE user_id = ?', (user_id,))
    rows = cursor.fetchall()
    total_volume = sum(s * r * w for s, r, w in rows)

    # Total distance covered from GPS-tracked runs/walks
    cursor.execute('SELECT COALESCE(SUM(distance), 0) FROM workouts WHERE user_id = ?', (user_id,))
    total_distance = cursor.fetchone()[0]

    # All distinct dates this user has logged a workout on
    cursor.execute('SELECT DISTINCT date FROM workouts WHERE user_id = ?', (user_id,))
    workout_dates = set(row[0] for row in cursor.fetchall())
    conn.close()

    # Current streak: count back from today, day by day, while a workout exists
    streak = 0
    day = datetime.now().date()
    while day.isoformat() in workout_dates:
        streak += 1
        day = day - timedelta(days=1)

    # Achievements: simple threshold badges
    achievements = []
    if total_workouts >= 1:
        achievements.append("🎉 First Workout Logged")
    if total_workouts >= 10:
        achievements.append("🏋️ 10 Workouts Club")
    if total_workouts >= 50:
        achievements.append("💪 50 Workouts Club")
    if streak >= 3:
        achievements.append("🔥 3 Day Streak")
    if streak >= 7:
        achievements.append("🔥🔥 7 Day Streak")
    if total_volume >= 1000:
        achievements.append("🏆 1,000 kg Total Volume")
    if total_volume >= 10000:
        achievements.append("🏆🏆 10,000 kg Total Volume")
    if total_distance >= 5:
        achievements.append("🏃 5 km Club")
    if total_distance >= 42.2:
        achievements.append("🏃‍♂️ Marathon Distance")

    return {
        'total_workouts': total_workouts,
        'total_volume': round(total_volume, 1),
        'total_distance': round(total_distance, 2),
        'streak': streak,
        'achievements': achievements
    }

# ---- Helper: require login for a route ----
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

# ---- Auth routes ----
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        password_hash = generate_password_hash(password)

        conn = sqlite3.connect('workouts.db')
        cursor = conn.cursor()
        try:
            cursor.execute(
                'INSERT INTO users (email, password_hash) VALUES (?, ?)',
                (email, password_hash)
            )
            conn.commit()
            conn.close()
            return redirect('/login')
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('signup.html', error="That email is already registered.")

    return render_template('signup.html', error=None)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = sqlite3.connect('workouts.db')
        cursor = conn.cursor()
        cursor.execute('SELECT id, password_hash FROM users WHERE email = ?', (email,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user[1], password):
            session['user_id'] = user[0]
            session['email'] = email
            return redirect('/')
        else:
            return render_template('login.html', error="Invalid email or password.")

    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ---- Workout routes (now require login) ----
@app.route('/')
@login_required
def home():
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM workouts WHERE user_id = ? ORDER BY id DESC', (session['user_id'],))
    workouts = cursor.fetchall()
    conn.close()
    stats = get_stats(session['user_id'])
    return render_template('index.html', workouts=workouts, email=session.get('email'), stats=stats)

@app.route('/add', methods=['POST'])
@login_required
def add_workout():
    exercise = request.form['exercise']
    sets = request.form['sets']
    reps = request.form['reps']
    weight = request.form['weight']

    today = datetime.now().date().isoformat()

    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO workouts (user_id, exercise, sets, reps, weight, date) VALUES (?, ?, ?, ?, ?, ?)',
        (session['user_id'], exercise, sets, reps, weight, today)
    )
    conn.commit()
    conn.close()
    return redirect('/')

@app.route('/add_run', methods=['POST'])
@login_required
def add_run():
    distance = request.form['distance']
    today = datetime.now().date().isoformat()

    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO workouts (user_id, exercise, sets, reps, weight, distance, date) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (session['user_id'], 'Run / Walk (GPS)', 0, 0, 0, distance, today)
    )
    conn.commit()
    conn.close()
    return redirect('/')

@app.route('/delete/<int:workout_id>', methods=['POST'])
@login_required
def delete_workout(workout_id):
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    # The "AND user_id = ?" check matters: it stops someone from deleting
    # another user's workout just by guessing an ID in the URL.
    cursor.execute('DELETE FROM workouts WHERE id = ? AND user_id = ?', (workout_id, session['user_id']))
    conn.commit()
    conn.close()
    return redirect('/')

@app.route('/edit/<int:workout_id>', methods=['GET', 'POST'])
@login_required
def edit_workout(workout_id):
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()

    if request.method == 'POST':
        exercise = request.form['exercise']
        sets = request.form['sets']
        reps = request.form['reps']
        weight = request.form['weight']
        cursor.execute(
            'UPDATE workouts SET exercise = ?, sets = ?, reps = ?, weight = ? WHERE id = ? AND user_id = ?',
            (exercise, sets, reps, weight, workout_id, session['user_id'])
        )
        conn.commit()
        conn.close()
        return redirect('/')

    cursor.execute('SELECT * FROM workouts WHERE id = ? AND user_id = ?', (workout_id, session['user_id']))
    workout = cursor.fetchone()
    conn.close()
    if workout is None:
        return redirect('/')
    return render_template('edit.html', workout=workout)

@app.route('/exercise/<exercise_name>')
@login_required
def exercise_history(exercise_name):
    conn = sqlite3.connect('workouts.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM workouts WHERE user_id = ? AND exercise = ? ORDER BY date ASC',
        (session['user_id'], exercise_name)
    )
    entries = cursor.fetchall()
    conn.close()
    return render_template('exercise.html', exercise_name=exercise_name, entries=entries)

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5001))
    debug_mode = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
