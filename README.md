# Vape Workshop
Storefront + shop/repair manager. Python 3.8+, no installs.

**Windows:** double-click `start.bat`. Your browser opens by itself. Keep the black window open while you work.
**Or:** run `python app.py` and open http://localhost:8000

Keep `app.py`, `index.html` and `start.bat` in the same folder. `app.py` must be running for sign-in to work;
if the page is opened another way (Live Server, a double-clicked file) it finds `app.py` on port 8000 by itself.
Optional settings: MIN_AGE=18  PORT=9000  ADMIN_PASSWORD=yourpass. (MIN_AGE only applies to customers saved earlier with a date of birth.) Data lives in workshop.db.

## Signing in
- **First start only:** the password is saved hashed in the database. ADMIN_PASSWORD sets it; if you don't set it, the old default is used and a warning is printed. Change it right away (below).
- The first start also prints a **recovery key** in the server window. Write it down; it is shown once.
- **Remember me:** keeps you signed in for 30 days on that browser. Without it you are signed out when the tab closes (or after 12 hours).
- **Forgot password?** Enter the recovery key and a new password. This signs everyone out and gives you a new recovery key.
- **Lost the recovery key too?** On the shop computer run: `python app.py reset-password`
- After 8 wrong attempts the sign-in is locked for 10 minutes.

## Shop page and photos
- **Shop** (second tab) shows every product as a card with its photo, price, a "New" badge and stock warnings. Filter with the category buttons or the search box. **Add to sale** fills the cart; **Go to checkout** opens the Sales tab.
- **Edit** on a card changes the name, price, cost, photo and "New" badge. Photos are resized in the browser and stored in the database (nothing extra to install). You can also paste an https photo link when creating a product.
- The Inventory tab no longer shows cost or price. It is for checking and adding stock. Prices are edited from the Shop page.

## Shop page
- **Shop by category:** six banner tiles (Disposables, Pod System, Premium E-liquid, Egyptian E-liquid, Coils, Accessories). Click one to show only that category; click it again to show everything.
- **Your own banner:** hover a tile (always visible on phones) and press **Edit picture** to upload your own wide banner. **Use the drawn banner instead** goes back to the built-in one. Only use pictures you have permission to use.
- **Add product:** type the name, choose the **Color** and category, set the price and cost, and add a photo if you have one. SKU is optional and made automatically.
- **No photo?** The shop draws the item in its color. The color comes from the Color box, or from a color word in the name ("Blue Nexlime 2"). The picture updates live while you type.
- Make one product per color ("Nexlime 2 Blue", "Nexlime 2 Red"). Each gets its own picture and stock.
- **New badge:** automatic. An item shows the blue "New" circle for 14 days after you add it.
- **Edit** on a card also has **Delete**. A product that was already sold cannot be deleted (reports need it). Set its stock to 0 instead.
- If the system still has the old sample products, a **Remove sample products** button appears.

## Repairs
- Two columns: **Received** and **Completed**. Press **Mark completed** when the device is done, or **Move back** to reopen it.
- **Photo of the device:** choose a picture when you book the device in, or press **Add photo** / **Photo** on a card later. Click the small picture to see it large, replace it or remove it. Repair photos are only shown to signed-in staff.
- Repairs that were in the old Diagnosing / Repairing columns are now Received. Ready / Collected are now Completed.

## Customers and sales
- The date of birth box is gone. Look at the customer's photo ID and tick **I checked photo ID**. A sale cannot be completed for a customer until it is ticked.
- **Sales list:** the stock column is gone. Use **−** and **+** to change how many of an item are in the sale (it stops at what you have in stock). The same − / + appears on the Shop cards and in the Current sale box.
- **Discount:** in the Current sale box type a number and choose **L.E.** (an amount) or **%**. The total updates as you type. Reports show the money you actually received, after the discount.

## Hover
When you point at a card, tile, button or table row it lifts and catches the light. This is switched off automatically for people who have "reduce motion" turned on.

## Start automatically (no more running it in VS Code)
Run this once, from the folder that has app.py and index.html:

    Mac:      python3 install_autostart.py
    Windows:  python install_autostart.py

- Stop the server you started in VS Code first (click its terminal, press Ctrl+C), or the installer will tell you the port is busy.
- It copies the program and your data to a folder called `DripHouse` in your home folder and starts the server in the background every time you sign in. Then just open http://localhost:8000 (bookmark it).
- **To update:** replace app.py / index.html in your project folder and run the installer again. It restarts the server and never overwrites your database.
- **Switch it off:** `python3 install_autostart.py remove` (your data stays).
- Problems? Open `server.log` in the DripHouse folder; it shows what the server said.
