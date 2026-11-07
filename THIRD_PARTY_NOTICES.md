# Third-Party Notices

This distribution includes integration and installer support derived from
[DeepTutor](https://github.com/HKUDS/DeepTutor) `1.7.1`, commit
`f7aed0744c36c5afef16a4261c31ebee1208cac2`. DeepTutor is licensed under the
Apache License, Version 2.0; the accompanying `LICENSE` file contains its
license text.

`deeptutor-link` is designed to be installed as an extension beside that pinned
host. Its installer can patch a verified local host checkout to mount the
extension routes and install recovery guards. The derived Docker build uses the
official pinned DeepTutor image as its base and adds this extension wheel; it
does not reproduce or replace the DeepTutor source in this repository.

