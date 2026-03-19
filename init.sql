-- Create the database if it doesn't exist (fallback for some environments)
CREATE DATABASE IF NOT EXISTS birthday_db;
USE birthday_db;

-- Create the table for storing birthdays
CREATE TABLE IF NOT EXISTS birthdays (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(100) NOT NULL, -- Telegram Chat ID
    name VARCHAR(255) NOT NULL,    -- Name of the person
    birthday DATE NOT NULL,        -- Their birth date
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;