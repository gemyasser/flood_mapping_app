import ee
import streamlit as st
import geemap.foliumap as geemap
import os
import geopandas as gpd
import zipfile
import tempfile
import pandas as pd

# Authenticate and initialize Earth Engine
ee.Authenticate()
ee.Initialize(project='ee-mo7yasser1')
geemap.ee_initialize()

# Set up Streamlit layout
st.set_page_config(layout="wide")

# Add the logo to the sidebar
st.sidebar.image("solafune_logo.png", use_container_width=True)

# Sidebar content
st.sidebar.header("Flood Mapping Tool")


# File uploader for zip file containing shapefile components
st.sidebar.markdown(
    """
    **1-Upload ZIP file (AOI):**  
    The zip file should include the following files:  
    .shp, .shx, .prj, .dbf, .cpg
    """
)

uploaded_zip = st.sidebar.file_uploader("Choose a ZIP file", type=["zip"])

# Date range selection for pre-flood dates
st.sidebar.header("2-Range Pre-flood dates")

# Using st.columns to display two date inputs on one line
col1, col2 = st.sidebar.columns([1, 1])
with col1:
    start_date = st.date_input("Start Date", value=pd.to_datetime('2024-01-23'))
with col2:
    end_date = st.date_input("End Date", value=pd.to_datetime('2024-01-24'))

# Date range selection for post-flood dates
st.sidebar.header("3-Range Post-flood dates")

# Using st.columns to display two date inputs for post-flood dates
col3, col4 = st.sidebar.columns([1, 1])
with col3:
    start_date_post = st.date_input("Start Date", value=pd.to_datetime('2024-11-23'))
with col4:
    end_date_post = st.date_input("End Date", value=pd.to_datetime('2024-11-24'))

# Function to extract and load shapefile from a zip file
def load_shapefile_from_zip(zip_file):
    with tempfile.TemporaryDirectory() as tmpdirname:
        # Create a path to save the uploaded zip file
        zip_path = os.path.join(tmpdirname, "shapefile.zip")
        
        # Save the uploaded zip file to the temporary directory
        with open(zip_path, "wb") as f:
            f.write(zip_file.getvalue())

        # Extract the zip file contents
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(tmpdirname)
        
        # Look for the shapefile components (.shp, .shx, .dbf)
        shp_file = None
        for file in zip_ref.namelist():
            if file.endswith(".shp"):
                shp_file = file
                break
        
        if shp_file:
            # Read the shapefile using geopandas
            study_extent = gpd.read_file(os.path.join(tmpdirname, shp_file))
            return study_extent
        else:
            st.error("Shapefile (.shp) not found in the uploaded ZIP file.")
            return None

# Default map location (center of the world or a predefined region)
default_lat, default_lon = 0, 0  # Center of the world
default_zoom = 2  # World zoom level

# Main content - map
Map = geemap.Map(center=[default_lat, default_lon], zoom=default_zoom)

# Function to get Sentinel-2 data for a specific date range
def get_S2(roi, start_date, end_date):
    start_date_str = start_date.strftime('%Y-%m-%d')
    end_date_str = end_date.strftime('%Y-%m-%d')
    
    dataset = ee.ImageCollection("COPERNICUS/S2_HARMONIZED") \
        .filterDate(ee.Date(start_date_str), ee.Date(end_date_str)) \
        .filterBounds(roi)  # Filter by region of interest (ROI)
    
    s2_clipped = dataset.map(lambda image: image.clip(roi))

    # Check if the collection is empty
    if s2_clipped.size().getInfo() == 0:
        st.warning(f"No Sentinel-2 images found for the selected date range: {start_date_str} to {end_date_str}")
        return None, None
    
    # Visualization parameters for RGB
    vis_params = {
        'bands': ['B4', 'B3', 'B2'], 
        'min': 0,
        'max': 3000
    }
    
    return s2_clipped.mean().visualize(**vis_params), s2_clipped

# Function to calculate MNDWI
def calculate_mndwi(image):
    mndwi = image.normalizedDifference(['B3', 'B11']).rename('MNDWI')
    return image.addBands(mndwi)

# Function to create water mask
def water_mask(image, threshold):
    mask = image.gt(threshold).rename("water_mask").selfMask()
    return mask

# Check if the user uploaded a zip file
if uploaded_zip is not None:
    # Load the shapefile from the uploaded zip file
    study_extent = load_shapefile_from_zip(uploaded_zip)
    
    if study_extent is not None:
        # Reproject to a projected CRS (e.g., WGS84 Lat/Lon)
        study_extent = study_extent.to_crs(epsg=4326)

        # Convert GeoDataFrame to Earth Engine object
        roi = geemap.geopandas_to_ee(study_extent)

        # Update the map with the AOI
        Map = geemap.Map(center=[study_extent.geometry.centroid.y.mean(), study_extent.geometry.centroid.x.mean()], zoom=9)
        Map.addLayer(roi, {'color': 'red'}, "AOI")

        # Get Sentinel-2 data for the selected pre-flood date range and region
        pre_rgb_layer, s2_preflood_clipped = get_S2(roi, start_date, end_date)
        if pre_rgb_layer is not None:
            Map.addLayer(pre_rgb_layer, {}, "Pre-flood RGB")

            # Calculate MNDWI and extract permanent water
            preflood_mndwi = s2_preflood_clipped.map(calculate_mndwi).select('MNDWI').mean()
            perm_water = water_mask(preflood_mndwi, 0.01)

            # Get Sentinel-2 data for the selected post-flood date range and region
            post_rgb_layer, s2_postflood_clipped = get_S2(roi, start_date_post, end_date_post)
            if post_rgb_layer is not None:
                Map.addLayer(post_rgb_layer, {}, "Post-flood RGB")

                # Calculate MNDWI and extract flooded extent
                postflood_mndwi = s2_postflood_clipped.map(calculate_mndwi).select('MNDWI').mean()
                flood_water = water_mask(postflood_mndwi, 0.01)

                # Mask out Permanent water bodies to get JUST the flood extent
                notPermWaterMask = preflood_mndwi.lt(0.01).selfMask()
                flooded = flood_water.updateMask(notPermWaterMask)

                # Visualization parameters
                mndwi_Viz = {
                    "min": 0.01, 
                    "max": 0.8, 
                    "palette": ["#f7fbff", "#1452d9"]  # Light blue to deep blue
                }

                water_Viz = {
                    "min": 0.0, 
                    "max": 0.9, 
                    "palette": ["#f7fbff", "#1452d9"]
                }

                # Add layers to the map
                Map.addLayer(flooded, water_Viz, "Flood Extent")
                
                Map.add_legend(
                    title="Flood Extent",
                    legend_dict={
                        "Flooded Areas": "#1452d9"  # Blue color for flooded areas
                    }
                )

Map.to_streamlit()


