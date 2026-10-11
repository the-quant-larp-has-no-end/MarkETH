Pieces as a browser's MediaRecorder hands them over, one file per `dataavailable` Blob,
unchanged and in order: what a studio sends to the media box, one upload per piece. The
image test of the box (tests/integration/test_mediabox_image.py, `uv run pytest -m media`)
feeds each fixture to the real ffmpeg through the box's socket.

A fixture is a directory: 000.bin, 001.bin, ... and manifest.json, which says what the
browser reported (its version, the type it recorded, the picture's size, whether there is
sound, the size of each piece and when it arrived). The test reads the size and the sound
from it. frontend/e2e/record-fixtures.mjs records a fixture and replaces its directory:

    cd frontend && node e2e/record-fixtures.mjs                        chrome-webm, chrome-mp4
    cd frontend && node e2e/record-fixtures.mjs firefox-webm
    cd frontend && node e2e/record-fixtures.mjs webkit-mp4
    cd frontend && node e2e/record-fixtures.mjs --by-hand safari-mp4

Every fixture is ten seconds at 320 x 180 with a piece asked for every second.

chrome-webm/   video/webm;codecs=vp8,opus from Google Chrome 154 on macOS, its stand-in
               camera and microphone: ten pieces, one a second. 000 carries the EBML
               header, the tracks and the first cluster; every later piece begins in the
               middle of an element and is not decodable alone.
chrome-mp4/    video/mp4;codecs=avc1.42E01F,mp4a.40.2 from the same Chrome. Chrome hands
               MP4 over once per key frame interval (five seconds here), whatever was
               asked: 000 is ftyp and moov, 001 is two fragments (moof, mdat, moof, mdat).
               It does not prove Safari's bytes.
webkit-mp4/    video/mp4;codecs=avc1.42E01F,mp4a.40.2 from Playwright's WebKit 27.2 on
               macOS (`npx playwright install webkit`): Safari's engine and the system's
               encoder, not Safari. A drawn picture without sound, because that browser
               has no stand-in camera and its sound does not start without a tap. Nine
               pieces, the first after two seconds, then one a second: 000 is ftyp, moov
               and the first fragment, every later piece is one moof and one mdat.

Not recorded, so their cases of the image test are skipped and what these browsers send
is not proved:

firefox-webm/  Playwright's Firefox (`npx playwright install firefox`), its stand-in
               camera and microphone.
safari-mp4/    Safari on a Mac, by hand, with the camera and the microphone: the script
               serves the page and waits while a person opens the address it prints and
               taps "Record". Sound from an Apple recorder is proved by this fixture alone.

beside_the_box.py is the part of the image test that runs in a container beside the box.
