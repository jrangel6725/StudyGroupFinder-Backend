"""Flask API for the Study Group Finder Phase 1 backend."""

import os
from functools import wraps
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from flask import Flask, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db_connection


load_dotenv(Path(__file__).with_name(".env"))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-this"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


def json_body():
    """Return a JSON request body, or an empty object for a missing body."""
    return request.get_json(silent=True) or {}


def login_required(route):
    @wraps(route)
    def wrapped(*args, **kwargs):
        if "student_id" not in session:
            return jsonify({"error": "You must be logged in."}), 401
        return route(*args, **kwargs)

    return wrapped


def close_db(connection, cursor=None):
    if cursor is not None:
        cursor.close()
    if connection is not None and connection.is_connected():
        connection.close()


@app.get("/api/health")
def health():
    """Verify that Flask can open the configured MySQL database."""
    connection = None
    try:
        connection = get_db_connection()
        return jsonify({"status": "ok", "database": "connected"})
    except (mysql.connector.Error, RuntimeError) as error:
        return jsonify({"status": "error", "database": str(error)}), 503
    finally:
        close_db(connection)


@app.get("/api/courses")
def list_courses():
    """Return the course list used by the Phase 1 frontend."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT course_id, course_code, course_name
            FROM courses
            ORDER BY course_code, course_name
            """
        )
        return jsonify({"courses": cursor.fetchall()})
    except (mysql.connector.Error, RuntimeError) as error:
        return jsonify({"error": str(error)}), 500
    finally:
        close_db(connection, cursor)


@app.post("/api/auth/signup")
def signup():
    data = json_body()
    required = (
        "first_name",
        "last_name",
        "email",
        "password",
        "date_of_birth",
        "gender",
    )
    missing = [field for field in required if not str(data.get(field, "")).strip()]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400
    if len(data["password"]) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO students
                (student_email, password_hash, first_name, last_name,
                 date_of_birth, gender, major, phone_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                data["email"].strip().lower(),
                generate_password_hash(data["password"]),
                data["first_name"].strip(),
                data["last_name"].strip(),
                data["date_of_birth"],
                data["gender"].strip(),
                data.get("major"),
                data.get("phone_number"),
            ),
        )
        connection.commit()
        student_id = cursor.lastrowid
        session["student_id"] = student_id
        session["email"] = data["email"].strip().lower()
        return jsonify({"message": "Account created.", "student_id": student_id}), 201
    except mysql.connector.IntegrityError:
        if connection is not None:
            connection.rollback()
        return jsonify({"error": "An account with that email already exists."}), 409
    except (mysql.connector.Error, RuntimeError) as error:
        if connection is not None:
            connection.rollback()
        return jsonify({"error": str(error)}), 500
    finally:
        close_db(connection, cursor)


@app.post("/api/auth/login")
def login():
    data = json_body()
    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT student_id, first_name, last_name, student_email, password_hash
            FROM students
            WHERE student_email = %s
            """,
            (email,),
        )
        student = cursor.fetchone()
        if student is None or not check_password_hash(student["password_hash"], password):
            return jsonify({"error": "Invalid email or password."}), 401

        session["student_id"] = student["student_id"]
        session["email"] = student["student_email"]
        student.pop("password_hash", None)
        return jsonify({"message": "Logged in.", "student": student})
    except (mysql.connector.Error, RuntimeError) as error:
        return jsonify({"error": str(error)}), 500
    finally:
        close_db(connection, cursor)


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"message": "Logged out."})


@app.get("/api/auth/me")
@login_required
def current_student():
    return jsonify({"student_id": session["student_id"], "email": session.get("email")})


@app.get("/api/groups")
def list_groups():
    course_id = request.args.get("course_id")
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT g.group_id, g.group_name, g.description, g.course_id,
                   g.creator_id, g.leader_id, g.meeting_format,
                   g.meeting_location, g.maximum_capacity,
                   COUNT(gm.student_id) AS member_count
            FROM study_groups g
            LEFT JOIN group_members gm ON gm.group_id = g.group_id
        """
        params = []
        if course_id:
            query += " WHERE g.course_id = %s"
            params.append(course_id)
        query += " GROUP BY g.group_id ORDER BY g.group_name"
        cursor.execute(query, tuple(params))
        return jsonify({"groups": cursor.fetchall()})
    except (mysql.connector.Error, RuntimeError) as error:
        return jsonify({"error": str(error)}), 500
    finally:
        close_db(connection, cursor)


@app.post("/api/groups")
@login_required
def create_group():
    data = json_body()
    required = ("course_id", "group_name", "meeting_format", "maximum_capacity")
    missing = [field for field in required if data.get(field) in (None, "")]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    try:
        maximum_capacity = int(data["maximum_capacity"])
        if maximum_capacity < 1:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "maximum_capacity must be a positive integer."}), 400

    if data["meeting_format"] not in {"in_person", "online", "hybrid"}:
        return jsonify({"error": "meeting_format must be in_person, online, or hybrid."}), 400

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        connection.start_transaction()
        cursor.execute(
            """
            INSERT INTO study_groups
                (course_id, creator_id, leader_id, group_name, description,
                 meeting_format, meeting_location, maximum_capacity)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                data["course_id"],
                session["student_id"],
                session["student_id"],
                str(data["group_name"]).strip(),
                str(data.get("description", "")).strip(),
                data["meeting_format"],
                data.get("meeting_location"),
                maximum_capacity,
            ),
        )
        group_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO group_members (group_id, student_id)
            VALUES (%s, %s)
            """,
            (group_id, session["student_id"]),
        )
        connection.commit()
        return jsonify({"message": "Group created.", "group_id": group_id}), 201
    except (mysql.connector.Error, RuntimeError) as error:
        if connection is not None:
            connection.rollback()
        return jsonify({"error": str(error)}), 500
    finally:
        close_db(connection, cursor)


@app.post("/api/groups/<int:group_id>/join")
@login_required
def join_group(group_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        connection.start_transaction()

        # Lock the group row so concurrent joins cannot exceed max_members.
        cursor.execute(
            """
            SELECT group_id, maximum_capacity
            FROM study_groups
            WHERE group_id = %s
            FOR UPDATE
            """,
            (group_id,),
        )
        group = cursor.fetchone()
        if group is None:
            connection.rollback()
            return jsonify({"error": "Group not found."}), 404

        cursor.execute(
            """
            SELECT COUNT(*) AS member_count
            FROM group_members
            WHERE group_id = %s
            """,
            (group_id,),
        )
        member_count = cursor.fetchone()["member_count"]
        if member_count >= group["maximum_capacity"]:
            connection.rollback()
            return jsonify({"error": "This group is already full."}), 409

        cursor.execute(
            """
            SELECT 1 FROM group_members
            WHERE group_id = %s AND student_id = %s
            """,
            (group_id, session["student_id"]),
        )
        if cursor.fetchone() is not None:
            connection.rollback()
            return jsonify({"error": "You are already a member of this group."}), 409

        cursor.execute(
            """
            INSERT INTO group_members (group_id, student_id)
            VALUES (%s, %s)
            """,
            (group_id, session["student_id"]),
        )
        connection.commit()
        return jsonify({"message": "Joined group."}), 201
    except mysql.connector.IntegrityError:
        if connection is not None:
            connection.rollback()
        return jsonify({"error": "You are already a member of this group."}), 409
    except (mysql.connector.Error, RuntimeError) as error:
        if connection is not None:
            connection.rollback()
        return jsonify({"error": str(error)}), 500
    finally:
        close_db(connection, cursor)


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "0") == "1")
