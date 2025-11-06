"""
Seed script to populate the database with dummy data for development.
Creates 1 admin user and 10 regular users with posts and reactions.
"""

import asyncio
import aiosqlite
from auth import hash_password
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
import random

load_dotenv()
DATABASE_PATH = os.getenv('DATABASE_PATH')

# User credentials
ADMIN_USER = {
    'username': 'admin',
    'password': 'admin123',  # Plain text - will be hashed
    'is_admin': 1
}

DUMMY_USERS = [
    {'username': 'sarah_johnson', 'password': 'user123'},
    {'username': 'mike_chen', 'password': 'user123'},
    {'username': 'emma_davis', 'password': 'user123'},
    {'username': 'alex_rodriguez', 'password': 'user123'},
    {'username': 'jessica_kim', 'password': 'user123'},
    {'username': 'david_miller', 'password': 'user123'},
    {'username': 'olivia_brown', 'password': 'user123'},
    {'username': 'james_wilson', 'password': 'user123'},
    {'username': 'sophia_martinez', 'password': 'user123'},
    {'username': 'ryan_taylor', 'password': 'user123'}
]

# Sample post content
POST_CONTENTS = [
    "Just finished an amazing book! Highly recommend 'The Midnight Library' 📚",
    "Beautiful sunset today at the beach 🌅",
    "Anyone else excited for the weekend? Can't wait to relax!",
    "Coffee tastes better on Monday mornings ☕",
    "Working on a new project and feeling motivated!",
    "Throwback to my trip to Japan last year 🇯🇵",
    "Does anyone have good restaurant recommendations downtown?",
    "Finally tried that new pizza place everyone's been talking about. Worth the hype!",
    "Rainy days are perfect for staying in and watching movies 🎬",
    "Just hit a new personal record at the gym! 💪",
    "Anyone want to grab coffee this week?",
    "Can't believe it's already December! Time flies",
    "This weather is absolutely perfect today",
    "Started learning guitar and it's harder than I thought 🎸",
    "Best tacos I've ever had! Thanks for the recommendation @friends",
    "Sunday morning vibes ☀️",
    "New year, new goals. What's everyone working on?",
    "Missing live concerts so much right now 🎵",
    "That movie ending was NOT what I expected 😱",
    "Productive day at work! Feeling accomplished",
    "Who else is binge-watching this new show?",
    "Grateful for good friends and good times",
    "Why is adulting so hard sometimes? 😅",
    "Found a new favorite coffee shop in the neighborhood",
    "Weekend plans: absolutely nothing and I'm loving it",
    "Can't stop thinking about that incredible meal last night",
    "Nature walks are so underrated",
    "Finally organized my closet. It only took 6 months 😂",
    "Anyone else a night owl? It's 2am and I'm wide awake",
    "Just adopted a puppy! Meet Charlie 🐶"
]


async def seed_database():
    """Populate the database with dummy data"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        print("🌱 Starting database seeding...\n")

        # Create admin user
        print("Creating admin user...")
        admin_password_hash = hash_password(ADMIN_USER['password'])
        try:
            await db.execute(
                """INSERT INTO users (username, password_hash, is_admin, capacity)
                   VALUES (?, ?, ?, ?)""",
                (ADMIN_USER['username'], admin_password_hash, 1, 1024*1024*1024)
            )
            await db.commit()
            print(f"✓ Admin user created: {ADMIN_USER['username']}")
        except aiosqlite.IntegrityError:
            print(f"⚠ Admin user '{ADMIN_USER['username']}' already exists")

        # Create dummy users
        print("\nCreating dummy users...")
        user_ids = []
        for user in DUMMY_USERS:
            password_hash = hash_password(user['password'])
            try:
                cursor = await db.execute(
                    """INSERT INTO users (username, password_hash, capacity)
                       VALUES (?, ?, ?)""",
                    (user['username'], password_hash, 1024*1024*1024)
                )
                await db.commit()
                user_ids.append(cursor.lastrowid)
                print(f"✓ User created: {user['username']}")
            except aiosqlite.IntegrityError:
                print(f"⚠ User '{user['username']}' already exists")
                # Get existing user ID
                async with db.execute(
                    "SELECT id FROM users WHERE username = ?",
                    (user['username'],)
                ) as cursor:
                    result = await cursor.fetchone()
                    if result:
                        user_ids.append(result[0])

        # Get admin user ID
        async with db.execute(
            "SELECT id FROM users WHERE username = ?",
            (ADMIN_USER['username'],)
        ) as cursor:
            admin_result = await cursor.fetchone()
            if admin_result:
                admin_id = admin_result[0]

        # Create posts for each user
        print("\nCreating posts...")
        post_ids = []
        base_time = datetime.now() - timedelta(days=30)

        for i, user_id in enumerate(user_ids):
            # Each user gets 2-4 posts
            num_posts = random.randint(2, 4)
            for j in range(num_posts):
                content = random.choice(POST_CONTENTS)
                # Spread posts over the last 30 days
                post_time = base_time + timedelta(
                    days=random.randint(0, 30),
                    hours=random.randint(0, 23),
                    minutes=random.randint(0, 59)
                )

                cursor = await db.execute(
                    """INSERT INTO posts (user_id, post_type, content, created_at)
                       VALUES (?, 'text', ?, ?)""",
                    (user_id, content, post_time.isoformat())
                )
                await db.commit()
                post_ids.append(cursor.lastrowid)

        print(f"✓ Created {len(post_ids)} posts")

        # Create admin post
        cursor = await db.execute(
            """INSERT INTO posts (user_id, post_type, content, created_at)
               VALUES (?, 'text', ?, ?)""",
            (admin_id, "Welcome to the platform! Hope everyone enjoys their time here. 🎉", datetime.now().isoformat())
        )
        await db.commit()
        post_ids.append(cursor.lastrowid)
        print("✓ Created admin welcome post")

        # Add reactions (dislikes) - some posts get dislikes
        print("\nAdding reactions...")
        reaction_count = 0

        for post_id in post_ids:
            # 30% chance a post gets dislikes
            if random.random() < 0.3:
                # 1-3 users dislike this post
                num_dislikes = random.randint(1, min(3, len(user_ids)))
                disliking_users = random.sample(user_ids, num_dislikes)

                for user_id in disliking_users:
                    try:
                        await db.execute(
                            """INSERT INTO reactions (post_id, user_id, reaction_type)
                               VALUES (?, ?, 'dislike')""",
                            (post_id, user_id)
                        )
                        await db.commit()
                        reaction_count += 1
                    except aiosqlite.IntegrityError:
                        # User already reacted to this post
                        pass

        print(f"✓ Created {reaction_count} reactions")

        # Print summary
        print("\n" + "="*60)
        print("✅ Database seeding completed!")
        print("="*60)
        print("\n📊 Summary:")
        print(f"   • 1 Admin user")
        print(f"   • {len(DUMMY_USERS)} Regular users")
        print(f"   • {len(post_ids)} Total posts")
        print(f"   • {reaction_count} Reactions (dislikes)")

        print("\n🔑 User Credentials:")
        print("\n   ADMIN:")
        print(f"   Username: {ADMIN_USER['username']}")
        print(f"   Password: {ADMIN_USER['password']}")

        print("\n   REGULAR USERS (all use same password):")
        print("   Password: user123")
        print("\n   Usernames:")
        for i, user in enumerate(DUMMY_USERS, 1):
            print(f"   {i:2}. {user['username']}")

        print("\n" + "="*60)


async def main():
    """Main entry point"""
    if not DATABASE_PATH:
        print("❌ Error: DATABASE_PATH not found in environment variables")
        print("   Make sure your .env file is configured correctly")
        return

    if not os.path.exists(DATABASE_PATH):
        print(f"❌ Error: Database file not found at {DATABASE_PATH}")
        print("   Please run the application first to initialize the database")
        return

    print(f"📁 Using database: {DATABASE_PATH}\n")

    try:
        await seed_database()
    except Exception as e:
        print(f"\n❌ Error during seeding: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
