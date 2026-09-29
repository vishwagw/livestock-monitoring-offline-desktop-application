import math

import numpy as np
import pytest

from livestock_engine.camera import CameraModel, FramePose
from livestock_engine.geo import LocalUTM, utm_epsg, utm_zone
from livestock_engine.projection import camera_rotation, cast_to_ground, ground_to_pixel, in_image

CAM = CameraModel(4000, 3000, 84.0, "diagonal")


def test_focal_length_from_each_fov_type():
    h = CameraModel(4000, 3000, 90.0, "horizontal")
    assert h.focal_px == pytest.approx(2000.0)
    assert h.hfov_deg == pytest.approx(90.0)
    v = CameraModel(4000, 3000, 90.0, "vertical")
    assert v.focal_px == pytest.approx(1500.0)
    d = CameraModel(4000, 3000, 90.0, "diagonal")
    assert d.focal_px == pytest.approx(2500.0)


def test_invalid_camera_and_pose_rejected():
    with pytest.raises(ValueError):
        CameraModel(4000, 3000, 0.0)
    with pytest.raises(ValueError):
        CameraModel(4000, 3000, 80.0, "fisheye")
    with pytest.raises(ValueError):
        FramePose(0.0, 0.0, -5.0, 0.0)


@pytest.mark.parametrize(
    "lat, lon, epsg",
    [(-33.87, 151.21, 32756), (51.5, -0.12, 32630), (60.0, 5.3, 32632), (78.2, 15.6, 32633)],
)
def test_utm_zone_selection(lat, lon, epsg):
    assert utm_epsg(lat, lon) == epsg


def test_utm_zone_edges():
    assert utm_zone(0.0, 180.0) == 1
    assert utm_zone(0.0, 179.99) == 60


def test_utm_round_trip_and_convergence():
    utm = LocalUTM(-33.87, 151.21)
    e, n = utm.to_utm(151.21, -33.87)
    lon, lat = utm.to_geographic(e, n)
    assert lon == pytest.approx(151.21, abs=1e-9)
    assert lat == pytest.approx(-33.87, abs=1e-9)
    # Zero on the central meridian (153 E for zone 56), non-zero away from it.
    assert utm.grid_convergence_deg(153.0, -33.87) == pytest.approx(0.0, abs=1e-6)
    assert abs(utm.grid_convergence_deg(151.21, -33.87)) > 0.5


def test_nadir_principal_point_hits_ground_below_drone():
    rot = camera_rotation(37.0, -90.0)
    pts, valid = cast_to_ground(CAM, (100.0, 200.0, 60.0), rot, [2000.0], [1500.0])
    assert valid.all()
    np.testing.assert_allclose(pts[0], [100.0, 200.0], atol=1e-9)


def test_top_of_nadir_image_points_along_heading():
    rot = camera_rotation(90.0, -90.0)  # flying east
    pts, _ = cast_to_ground(CAM, (0.0, 0.0, 50.0), rot, [2000.0, 4000.0], [0.0, 1500.0])
    assert pts[0, 0] > 1.0 and abs(pts[0, 1]) < 1e-9  # image top -> east
    assert pts[1, 1] < -1.0 and abs(pts[1, 0]) < 1e-9  # image right -> south


def test_nadir_footprint_matches_field_of_view():
    h = 60.0
    rot = camera_rotation(0.0, -90.0)
    pts, _ = cast_to_ground(CAM, (0.0, 0.0, h), rot, [0.0, 4000.0], [1500.0, 1500.0])
    width = pts[1, 0] - pts[0, 0]
    assert width == pytest.approx(2 * h * math.tan(math.radians(CAM.hfov_deg) / 2))


def test_oblique_centre_ray_distance():
    rot = camera_rotation(0.0, -45.0)
    pts, _ = cast_to_ground(CAM, (0.0, 0.0, 50.0), rot, [2000.0], [1500.0])
    np.testing.assert_allclose(pts[0], [0.0, 50.0], atol=1e-9)


def test_rays_above_horizon_or_beyond_range_are_rejected():
    rot = camera_rotation(0.0, 0.0)  # looking at the horizon
    pts, valid = cast_to_ground(CAM, (0.0, 0.0, 50.0), rot, [2000.0, 2000.0], [0.0, 2999.0], max_range_m=1000)
    assert not valid[0] and np.isnan(pts[0]).all()  # above horizon
    rot = camera_rotation(0.0, -3.0)
    _, valid = cast_to_ground(CAM, (0.0, 0.0, 50.0), rot, [2000.0], [1500.0], max_range_m=500)
    assert not valid[0]  # hits ground ~955 m away


@pytest.mark.parametrize("heading, pitch, roll", [(0, -90, 0), (123, -70, 2.5), (271, -55, -4)])
def test_ground_to_pixel_inverts_ray_casting(heading, pitch, roll):
    rng = np.random.default_rng(1)
    rot = camera_rotation(heading, pitch, roll)
    origin = (500.0, -200.0, 75.0)
    u = rng.uniform(0, 4000, 50)
    v = rng.uniform(0, 3000, 50)
    pts, valid = cast_to_ground(CAM, origin, rot, u, v, max_range_m=1e6)
    pix, in_front = ground_to_pixel(CAM, origin, rot, pts[valid])
    assert in_front.all()
    np.testing.assert_allclose(pix, np.column_stack([u, v])[valid], atol=1e-6)
    assert in_image(CAM, pix).all()
