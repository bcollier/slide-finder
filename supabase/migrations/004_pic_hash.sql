-- Hash of the largest picture embedded in the slide (256-bit dHash, hex), for recognising the
-- same picture-only slide across courses at search time and in the slide view.
alter table sf_slides add column if not exists pic_hash text;
create index if not exists sf_slides_pic_hash on sf_slides (pic_hash);
