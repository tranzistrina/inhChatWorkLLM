from server import app
import work_routes
import os
app.run(host='127.0.0.1',port=int(os.getenv('PORT','6767')),debug=False)
