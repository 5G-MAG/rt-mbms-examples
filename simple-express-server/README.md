<h1 align="center">Simple Express Server</h1>

## Introduction

This simple HTTP server based on express.js can be used to statically host files.

## Installation

### Clone the repository

````
git clone --recurse-submodules https://github.com/5G-MAG/rt-mbms-examples
```` 

### Install dependencies

````
cd rt-mbms-examples/simple-express-server
npm install
````

## Running
### Start the webserver

````
npm start
````
### Check the webserver

Navigate to `http://localhost:3333/watchfolder/` to check that the webserver started correctly. You should see a
message `This worked!`.

## Configuration

By default, the server runs on port `3333`. If you need to change the port edit
the `bin/www` file and change the default port. For instance, to use port `5555`:

````
var port = normalizePort(process.env.PORT || '5555');
````

## Hosting files

All files located in the `public` folder are automatically hosted. Simply drop your files in this folder.
