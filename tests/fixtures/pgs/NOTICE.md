# PGS fixture provenance

`minimal.sup.b64` is the Base64 representation of
`examples/00_00_00_000__00_01_0_000_00000.sup` from
[dam-cav/img-to-pgs-sup](https://github.com/dam-cav/img-to-pgs-sup), pinned at
commit `f595c0b83cdb27fffb652140fc9501661acfb155`.

The upstream fixture and generator are distributed under the MIT License:

> Copyright (c) 2022 dam-cav
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

The test suite decodes the Base64 text into a temporary `.sup` file. Keeping
the repository fixture textual makes provenance review and source-distribution
packaging deterministic.
