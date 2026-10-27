# Vape Workshop
Storefront + shop/repair manager. Python 3.8+, no installs.

**Windows:** double-click `start.bat`. Your browser opens by itself. Keep the black window open while you work.
**Or:** run `python app.py` and open http://localhost:8000

Keep `app.py`, `index.html` and `start.bat` in the same folder. `app.py` must be running for sign-in to work;
if the page is opened another way (Live Server, a double-clicked file) it finds `app.py` on port 8000 by itself.
Optional settings: MIN_AGE=18  PORT=9000  ADMIN_PASSWORD=yourpass. Data lives in workshop.db.

## Signing in
- **First start only:** the password is saved hashed in the database. ADMIN_PASSWORD sets it; if you don't set it, the old default is used and a warning is printed. Change it right away (below).
- The first start also prints a **recovery key** in the server window. Write it down; it is shown once.
- **Remember me:** keeps you signed in for 30 days on that browser. Without it you are signed out when the tab closes (or after 12 hours).
- **Forgot password?** Enter the recovery key and a new password. This signs everyone out and gives you a new recovery key.
- **Lost the recovery key too?** On the shop computer run: `python app.py reset-password`
- After 8 wrong attempts the sign-in is locked for 10 minutes.
